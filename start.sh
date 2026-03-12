#!/bin/bash

cd "$(dirname "$0")"

# 检查是否已运行
if [ -f .pid ]; then
    pid=$(cat .pid)
    if ps -p $pid > /dev/null 2>&1; then
        echo "服务已在运行 (PID: $pid)"
        exit 1
    fi
fi

# 后台启动（取消 CLAUDECODE 变量，避免嵌套会话错误）
unset CLAUDECODE
nohup python -m src.main_websocket > log.log 2>&1 &
echo $! > .pid

echo "服务已启动 (PID: $!)"
echo "日志: tail -f log.log"
