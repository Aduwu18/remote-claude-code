"""
异常监控 Watchdog

监控任务执行状态，检测异常情况：
- 任务超时
- 进程卡死
- 异常状态推送到 Host
"""
import asyncio
import logging
import time
import threading
from typing import Optional, Callable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)


class WatchdogEvent(str, Enum):
    """监控事件类型"""
    TASK_TIMEOUT = "task_timeout"          # 任务超时
    PROCESS_STALLED = "process_stalled"    # 进程卡死
    ERROR_OCCURRED = "error_occurred"      # 错误发生
    TASK_COMPLETED = "task_completed"      # 任务完成
    TASK_STARTED = "task_started"          # 任务开始
    HEARTBEAT = "heartbeat"                # 心跳


@dataclass
class TaskInfo:
    """任务信息"""
    task_id: str
    chat_id: str
    start_time: float = field(default_factory=time.time)
    last_update: float = field(default_factory=time.time)
    status: str = "running"
    error: Optional[str] = None


class Watchdog:
    """
    异常监控器

    功能：
    1. 任务超时检测（默认 30 分钟）
    2. 进程健康检查
    3. 异常状态推送到 Host

    Usage:
        watchdog = Watchdog(
            timeout=1800,  # 30 分钟
            on_event=my_event_handler
        )
        watchdog.start()

        # 任务开始时
        task_id = watchdog.start_task("task-123", "chat-xxx")

        # 任务更新时
        watchdog.update_task(task_id)

        # 任务完成时
        watchdog.end_task(task_id, success=True)

        # 停止监控
        watchdog.stop()
    """

    def __init__(
        self,
        timeout: int = 1800,  # 30 分钟
        check_interval: int = 60,  # 1 分钟
        on_event: Callable[[WatchdogEvent, dict], None] = None,
    ):
        """
        初始化监控器

        Args:
            timeout: 任务超时时间（秒）
            check_interval: 检查间隔（秒）
            on_event: 事件回调函数
        """
        self.timeout = timeout
        self.check_interval = check_interval
        self.on_event = on_event

        self._tasks: dict[str, TaskInfo] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_heartbeat = time.time()

    def start(self):
        """启动监控"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info(f"Watchdog 已启动，超时时间: {self.timeout}s")

    def stop(self):
        """停止监控"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Watchdog 已停止")

    def start_task(self, task_id: str, chat_id: str) -> str:
        """
        开始监控任务

        Args:
            task_id: 任务 ID
            chat_id: 聊天 ID

        Returns:
            task_id
        """
        with self._lock:
            self._tasks[task_id] = TaskInfo(
                task_id=task_id,
                chat_id=chat_id,
                status="running",
            )

        self._emit_event(WatchdogEvent.TASK_STARTED, {
            "task_id": task_id,
            "chat_id": chat_id,
            "timestamp": datetime.now().isoformat(),
        })

        logger.debug(f"任务开始: {task_id}")
        return task_id

    def update_task(self, task_id: str):
        """
        更新任务活动时间

        Args:
            task_id: 任务 ID
        """
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].last_update = time.time()

    def end_task(self, task_id: str, success: bool = True, error: str = None):
        """
        结束任务

        Args:
            task_id: 任务 ID
            success: 是否成功
            error: 错误信息（如果失败）
        """
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].status = "completed" if success else "failed"
                self._tasks[task_id].error = error

        event = WatchdogEvent.TASK_COMPLETED if success else WatchdogEvent.ERROR_OCCURRED
        self._emit_event(event, {
            "task_id": task_id,
            "success": success,
            "error": error,
            "timestamp": datetime.now().isoformat(),
        })

        logger.debug(f"任务结束: {task_id}, 成功: {success}")

    def heartbeat(self):
        """发送心跳"""
        self._last_heartbeat = time.time()
        self._emit_event(WatchdogEvent.HEARTBEAT, {
            "timestamp": datetime.now().isoformat(),
        })

    def _monitor_loop(self):
        """监控循环"""
        while self._running:
            try:
                self._check_tasks()
                self._check_heartbeat()
            except Exception as e:
                logger.error(f"监控循环异常: {e}")

            time.sleep(self.check_interval)

    def _check_tasks(self):
        """检查任务状态"""
        now = time.time()
        timeout_tasks = []

        with self._lock:
            for task_id, task in self._tasks.items():
                if task.status == "running":
                    elapsed = now - task.last_update
                    if elapsed > self.timeout:
                        timeout_tasks.append((task_id, task))

        for task_id, task in timeout_tasks:
            logger.warning(f"任务超时: {task_id}, 聊天: {task.chat_id}")
            self._emit_event(WatchdogEvent.TASK_TIMEOUT, {
                "task_id": task_id,
                "chat_id": task.chat_id,
                "elapsed": now - task.start_time,
                "timestamp": datetime.now().isoformat(),
            })

            # 标记任务为超时
            with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id].status = "timeout"

    def _check_heartbeat(self):
        """检查心跳"""
        now = time.time()
        elapsed = now - self._last_heartbeat

        # 如果超过 2 倍检查间隔没有心跳，可能进程卡死
        if elapsed > self.check_interval * 2:
            logger.warning(f"心跳超时: {elapsed}s")
            self._emit_event(WatchdogEvent.PROCESS_STALLED, {
                "elapsed": elapsed,
                "timestamp": datetime.now().isoformat(),
            })

    def _emit_event(self, event: WatchdogEvent, data: dict):
        """发送事件"""
        if self.on_event:
            try:
                self.on_event(event, data)
            except Exception as e:
                logger.error(f"事件回调异常: {e}")

    def get_active_tasks(self) -> list[dict]:
        """获取活动任务列表"""
        with self._lock:
            return [
                {
                    "task_id": task.task_id,
                    "chat_id": task.chat_id,
                    "status": task.status,
                    "elapsed": time.time() - task.start_time,
                }
                for task in self._tasks.values()
                if task.status == "running"
            ]


# 全局单例
_watchdog: Optional[Watchdog] = None


def get_watchdog() -> Watchdog:
    """获取全局 Watchdog 单例"""
    global _watchdog
    if _watchdog is None:
        _watchdog = Watchdog()
    return _watchdog


def init_watchdog(
    timeout: int = 1800,
    check_interval: int = 60,
    on_event: Callable[[WatchdogEvent, dict], None] = None,
) -> Watchdog:
    """初始化全局 Watchdog"""
    global _watchdog
    _watchdog = Watchdog(
        timeout=timeout,
        check_interval=check_interval,
        on_event=on_event,
    )
    return _watchdog