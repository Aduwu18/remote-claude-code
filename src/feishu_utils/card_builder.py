"""
飞书卡片消息构建器

提供类型安全的卡片 JSON 构建工具

飞书卡片消息结构：
{
    "msg_type": "interactive",
    "content": {
        "config": { "wide_screen_mode": true },
        "header": {
            "title": { "tag": "plain_text", "content": "标题" }
        },
        "elements": [
            { "tag": "div", "text": { "tag": "lark_md", "content": "**内容**" } },
            { "tag": "action", "actions": [...] }
        ]
    }
}
"""
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field


@dataclass
class CardElement:
    """卡片元素基类"""
    tag: str = field(default="", init=False)

    def to_dict(self) -> dict:
        return {"tag": self.tag}


@dataclass
class DivElement(CardElement):
    """文本块元素"""
    text: str
    text_type: str = "lark_md"  # lark_md 或 plain_text

    def __post_init__(self):
        self.tag = "div"

    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "text": {
                "tag": self.text_type,
                "content": self.text
            }
        }


@dataclass
class ActionElement(CardElement):
    """操作区域元素（按钮等）"""
    actions: List[Dict[str, Any]]

    def __post_init__(self):
        self.tag = "action"

    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "actions": self.actions
        }


@dataclass
class DividerElement(CardElement):
    """分割线元素"""

    def __post_init__(self):
        self.tag = "hr"


@dataclass
class NoteElement(CardElement):
    """备注元素（灰色小字）"""
    text: str

    def __post_init__(self):
        self.tag = "note"

    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "elements": [
                {
                    "tag": "plain_text",
                    "content": self.text
                }
            ]
        }


@dataclass
class CardConfig:
    """卡片配置"""
    wide_screen_mode: bool = True
    enable_forward: bool = True

    def to_dict(self) -> dict:
        return {
            "wide_screen_mode": self.wide_screen_mode,
            "enable_forward": self.enable_forward
        }


@dataclass
class CardHeader:
    """卡片标题"""
    title: str
    template: str = ""  # 可选颜色主题：blue, wathet, turquoise, green, yellow, orange, red, carmine, violet, purple, indigo, grey

    def to_dict(self) -> dict:
        result = {
            "title": {
                "tag": "plain_text",
                "content": self.title
            }
        }
        if self.template:
            result["template"] = self.template
        return result


class CardBuilder:
    """
    卡片消息构建器

    使用示例：
        card = (CardBuilder()
            .set_header("🤖 Claude", "blue")
            .add_div("回复内容")
            .add_div("更多内容")
            .build())
    """

    def __init__(self):
        self._header: Optional[CardHeader] = None
        self._config: CardConfig = CardConfig()
        self._elements: List[CardElement] = []

    def set_header(self, title: str, template: str = "") -> "CardBuilder":
        """设置卡片标题"""
        self._header = CardHeader(title=title, template=template)
        return self

    def set_config(self, wide_screen_mode: bool = True, enable_forward: bool = True) -> "CardBuilder":
        """设置卡片配置"""
        self._config = CardConfig(wide_screen_mode=wide_screen_mode, enable_forward=enable_forward)
        return self

    def add_div(self, text: str, text_type: str = "lark_md") -> "CardBuilder":
        """添加文本块"""
        self._elements.append(DivElement(text=text, text_type=text_type))
        return self

    def add_divider(self) -> "CardBuilder":
        """添加分割线"""
        self._elements.append(DividerElement(tag="hr"))
        return self

    def add_note(self, text: str) -> "CardBuilder":
        """添加备注（灰色小字）"""
        self._elements.append(NoteElement(text=text))
        return self

    def add_action(self, actions: List[Dict[str, Any]]) -> "CardBuilder":
        """添加操作区域"""
        self._elements.append(ActionElement(actions=actions))
        return self

    def add_button(
        self,
        text: str,
        value: Dict[str, Any],
        button_type: str = "default",
        url: Optional[str] = None
    ) -> "CardBuilder":
        """
        添加按钮

        Args:
            text: 按钮文字
            value: 按钮点击时回传的值
            button_type: 按钮样式 (default, primary, danger)
            url: 可选，点击后跳转的链接

        Returns:
            CardBuilder
        """
        button = {
            "tag": "button",
            "text": {
                "tag": "plain_text",
                "content": text
            },
            "type": button_type,
            "value": value
        }
        if url:
            button["url"] = url

        # 如果已有 action 元素，添加到其中；否则创建新的
        if self._elements and isinstance(self._elements[-1], ActionElement):
            self._elements[-1].actions.append(button)
        else:
            self._elements.append(ActionElement(actions=[button]))
        return self

    def build(self) -> dict:
        """
        构建卡片 JSON

        Returns:
            飞书卡片消息的 content 字段内容
        """
        content: Dict[str, Any] = {
            "config": self._config.to_dict()
        }

        if self._header:
            content["header"] = self._header.to_dict()

        if self._elements:
            content["elements"] = [elem.to_dict() for elem in self._elements]

        return content


# ==================== 预定义卡片构建函数 ====================

def build_markdown_card(
    title: str,
    content: str,
    footer: Optional[str] = None,
    header_template: str = ""
) -> dict:
    """
    构建 Markdown 卡片

    Args:
        title: 卡片标题
        content: Markdown 内容
        footer: 可选的底部备注
        header_template: 标题颜色主题

    Returns:
        卡片 JSON
    """
    builder = CardBuilder()
    builder.set_header(title, header_template)
    builder.add_div(content, "lark_md")

    if footer:
        builder.add_divider().add_note(footer)

    return builder.build()


def build_status_card(
    status: str,
    details: Optional[str] = None,
    icon: str = "⏳",
    header_template: str = "blue"
) -> dict:
    """
    构建执行状态卡片

    Args:
        status: 状态标题
        details: 状态详情
        icon: 状态图标
        header_template: 标题颜色主题

    Returns:
        卡片 JSON
    """
    builder = CardBuilder()
    builder.set_header(f"{icon} {status}", header_template)

    if details:
        builder.add_div(details, "lark_md")

    return builder.build()


def build_permission_card(
    tool_name: str,
    tool_input: dict,
    chat_id: str,
    session_id: Optional[str] = None
) -> dict:
    """
    构建权限确认卡片（带按钮）

    Args:
        tool_name: 工具名称
        tool_input: 工具输入参数
        chat_id: 聊天 ID
        session_id: 可选的会话 ID

    Returns:
        卡片 JSON
    """
    import json

    # 格式化工具输入
    input_display = json.dumps(tool_input, ensure_ascii=False, indent=2)
    if len(input_display) > 500:
        input_display = input_display[:500] + "\n... (内容过长，已截断)"

    content = f"""**操作**: `{tool_name}`

**详情**:
```
{input_display}
```"""

    builder = CardBuilder()
    builder.set_header("🔒 权限确认", "red")
    builder.add_div(content, "lark_md")

    # 添加按钮
    approve_value = {
        "action": "permission_approve",
        "chat_id": chat_id
    }
    deny_value = {
        "action": "permission_deny",
        "chat_id": chat_id
    }

    if session_id:
        approve_value["session_id"] = session_id
        deny_value["session_id"] = session_id

    builder.add_button("✅ 允许", approve_value, "primary")
    builder.add_button("❌ 拒绝", deny_value, "danger")

    builder.add_note("点击按钮快速确认，或回复 y/n")

    return builder.build()


def build_command_result_card(
    title: str,
    content: str,
    success: bool = True
) -> dict:
    """
    构建命令结果卡片

    Args:
        title: 结果标题
        content: 结果内容
        success: 是否成功

    Returns:
        卡片 JSON
    """
    icon = "✅" if success else "❌"
    template = "green" if success else "red"

    builder = CardBuilder()
    builder.set_header(f"{icon} {title}", template)
    builder.add_div(content, "lark_md")

    return builder.build()


def build_help_card() -> dict:
    """
    构建帮助卡片

    Returns:
        卡片 JSON
    """
    content = """**管理命令**:
- `/ls` - 列出可用容器
- `/start <容器名>` - 进入容器会话
- `/enter <容器名>` - 进入容器会话（同上）
- `/exit` - 退出当前容器会话
- `/stop` - 停止当前会话
- `/help` - 显示帮助信息

**使用提示**:
1. 直接发送消息与 Claude 对话
2. 敏感操作需要确认
3. 使用 Markdown 格式化消息"""

    builder = CardBuilder()
    builder.set_header("📖 帮助中心", "blue")
    builder.add_div(content, "lark_md")

    return builder.build()


def build_container_list_card(containers: List[Dict[str, Any]]) -> dict:
    """
    构建容器列表卡片

    Args:
        containers: 容器信息列表 [{"name": "xxx", "status": "running"}, ...]

    Returns:
        卡片 JSON
    """
    if not containers:
        content = "暂无可用容器"
    else:
        lines = []
        for c in containers:
            status_icon = "🟢" if c.get("status") == "running" else "🔴"
            lines.append(f"- {status_icon} `{c.get('name', 'unknown')}`")
        content = "\n".join(lines)

    builder = CardBuilder()
    builder.set_header("🐳 容器列表", "blue")
    builder.add_div(content, "lark_md")
    builder.add_note(f"共 {len(containers)} 个容器 | 使用 /start <容器名> 进入")

    return builder.build()


def build_welcome_card(container_name: str) -> dict:
    """
    构建容器会话欢迎卡片

    Args:
        container_name: 容器名称

    Returns:
        卡片 JSON
    """
    content = f"""已连接到容器 **{container_name}**

现在你可以在这个群聊中与 Claude 交互，执行容器内的操作。

**提示**:
- 直接发送消息即可与 Claude 对话
- 发送 `/exit` 或「退出」结束会话"""

    builder = CardBuilder()
    builder.set_header("🚀 容器会话", "green")
    builder.add_div(content, "lark_md")

    return builder.build()