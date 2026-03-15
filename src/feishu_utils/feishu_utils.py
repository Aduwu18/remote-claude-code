import datetime
import re
import requests
import json
import os

app_id = os.getenv('APP_ID')
app_secret = os.getenv('APP_SECRET')

assert app_id and app_secret, 'app_id and app_secret is required'

def get_tenant_access_token():
    """
    获取飞书的tenant_access_token
    :return:
    """
    res = requests.post(url='https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal', json={"app_id": app_id, "app_secret": app_secret}).json()
    return res['app_access_token']

def get_headers(access_token):
    return {'Authorization': 'Bearer ' + access_token}

def reply_message(message_id, text, access_token=None):
    if access_token is None:
        access_token = get_tenant_access_token()
        
    url = 'https://open.feishu.cn/open-apis/im/v1/messages/{}/reply'.format(message_id)
    
    ret_data = {'text': text}
    
    body = {
        "msg_type": "text",
        "content": json.dumps(ret_data, ensure_ascii=False, indent=4),
        'uuid': str(datetime.datetime.now().timestamp())
    }
    res = requests.post(url, headers=get_headers(access_token), json=body).json()
    return res

def send_message(receive_id, text, access_token=None):
    if access_token is None:
        access_token = get_tenant_access_token()

    url = 'https://open.feishu.cn/open-apis/im/v1/messages'
    param = {'receive_id_type': 'chat_id'}

    ret_data = {'text':text}

    body = {
        'receive_id': receive_id,
        "msg_type": "text",
        "content": json.dumps(ret_data, ensure_ascii=False, indent=4),
        'uuid': str(datetime.datetime.now().timestamp())
    }
    res = requests.post(url, headers=get_headers(access_token), json=body, params=param).json()
    return res


def send_markdown_message(receive_id: str, text: str, title: str = "", access_token=None) -> dict:
    """
    发送 Markdown 格式消息（使用卡片渲染）

    支持的 Markdown 语法：
    - 标题、粗体、斜体
    - 链接 [text](url)
    - 代码块、行内代码
    - 有序/无序列表
    - 表格
    - 引用块

    Args:
        receive_id: 接收者 ID (chat_id)
        text: Markdown 内容
        title: 可选的卡片标题
        access_token: 访问令牌（可选）

    Returns:
        dict: API 响应
    """
    from src.feishu_utils.card_builder import CardBuilder

    if access_token is None:
        access_token = get_tenant_access_token()

    builder = CardBuilder()
    if title:
        builder.set_header(title, "blue")
    builder.add_div(text, "lark_md")

    return send_card_message(receive_id, builder.build(), access_token)

def get_department_member_list(department_id, access_token=None):
    if access_token is None:
        access_token = get_tenant_access_token()
        
    # 获取部门直属用户列表
    url = 'https://open.feishu.cn/open-apis/contact/v3/users/find_by_department'
    params = {'department_id': department_id}
    res = requests.get(url, headers=get_headers(access_token), params=params).json()
    if res['code'] !=0:
        raise Exception(f'get_department_member_list() get err res:{json.dumps(res)}')
    return res

def get_chats_member_list(chat_id, access_token=None):
    if access_token is None:
        access_token = get_tenant_access_token()

    # 先查看机器人是否在群里
    url = f'https://open.feishu.cn/open-apis/im/v1/chats/{chat_id}/members/is_in_chat'
    res = requests.get(url, headers=get_headers(access_token)).json()
    if res['code'] !=0 or not res['data']['is_in_chat']:
        return {"data" : {"items": []}}
        # raise Exception(f'get_chats_member_list() get err res:{json.dumps(res)}')

    # 获取群成员列表
    url = f'https://open.feishu.cn/open-apis/im/v1/chats/{chat_id}/members'
    res = requests.get(url, headers=get_headers(access_token)).json()

    if res['code'] !=0:
        raise Exception(f'get_chats_member_list() get err res:{json.dumps(res)}')
    return res


def create_group_chat(user_open_id: str, name: str, access_token=None) -> str:
    """
    创建群聊会话

    需要飞书应用开通 im:chat:write 权限

    Args:
        user_open_id: 用户 open_id
        name: 群聊名称

    Returns:
        chat_id: 新创建的群聊 chat_id

    Raises:
        Exception: 创建失败时抛出异常
    """
    if access_token is None:
        access_token = get_tenant_access_token()

    url = 'https://open.feishu.cn/open-apis/im/v1/chats'
    body = {
        "chat_mode": "group",
        "name": name,
        "user_id_list": [user_open_id]
    }
    res = requests.post(url, headers=get_headers(access_token), json=body).json()
    if res['code'] != 0:
        raise Exception(f'创建群聊失败: {json.dumps(res, ensure_ascii=False)}')
    return res['data']['chat_id']


def update_message(message_id: str, text: str, access_token=None) -> dict:
    """
    更新已发送消息的内容

    API: PATCH /im/v1/messages/:message_id
    文档: https://open.feishu.cn/document/server-docs/im-v1/message/update

    Args:
        message_id: 消息 ID
        text: 新的消息内容
        access_token: 访问令牌（可选）

    Returns:
        dict: API 响应
    """
    if access_token is None:
        access_token = get_tenant_access_token()

    url = f'https://open.feishu.cn/open-apis/im/v1/messages/{message_id}'
    body = {
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False)
    }
    res = requests.patch(url, headers=get_headers(access_token), json=body)
    return res.json()


def send_message_with_id(receive_id: str, text: str, access_token=None) -> dict:
    """
    发送消息并返回完整响应（包含 message_id）

    Args:
        receive_id: 接收者 ID (chat_id)
        text: 消息内容
        access_token: 访问令牌（可选）

    Returns:
        dict: API 响应，包含 {"code": 0, "data": {"message_id": "xxx"}}
    """
    if access_token is None:
        access_token = get_tenant_access_token()

    url = 'https://open.feishu.cn/open-apis/im/v1/messages'
    param = {'receive_id_type': 'chat_id'}

    body = {
        'receive_id': receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
        'uuid': str(datetime.datetime.now().timestamp())
    }

    res = requests.post(url, headers=get_headers(access_token), json=body, params=param)
    return res.json()


def send_card_message(receive_id: str, card_content: dict, access_token=None) -> dict:
    """
    发送卡片消息

    API: POST /im/v1/messages
    文档: https://open.feishu.cn/document/server-docs/im-v1/message/create

    Args:
        receive_id: 接收者 ID (chat_id)
        card_content: 卡片内容（使用 card_builder 构建的 JSON）
        access_token: 访问令牌（可选）

    Returns:
        dict: API 响应
    """
    if access_token is None:
        access_token = get_tenant_access_token()

    url = 'https://open.feishu.cn/open-apis/im/v1/messages'
    param = {'receive_id_type': 'chat_id'}

    body = {
        'receive_id': receive_id,
        "msg_type": "interactive",
        "content": json.dumps(card_content, ensure_ascii=False),
        'uuid': str(datetime.datetime.now().timestamp())
    }

    res = requests.post(url, headers=get_headers(access_token), json=body, params=param)
    return res.json()


def send_card_message_with_id(receive_id: str, card_content: dict, access_token=None) -> dict:
    """
    发送卡片消息并返回完整响应（包含 message_id）

    Args:
        receive_id: 接收者 ID (chat_id)
        card_content: 卡片内容（使用 card_builder 构建的 JSON）
        access_token: 访问令牌（可选）

    Returns:
        dict: API 响应，包含 {"code": 0, "data": {"message_id": "xxx"}}
    """
    return send_card_message(receive_id, card_content, access_token)


def update_card_message(message_id: str, card_content: dict, access_token=None) -> dict:
    """
    更新已发送的卡片消息

    API: PATCH /im/v1/messages/:message_id
    文档: https://open.feishu.cn/document/server-docs/im-v1/message/update

    Args:
        message_id: 消息 ID
        card_content: 新的卡片内容
        access_token: 访问令牌（可选）

    Returns:
        dict: API 响应
    """
    if access_token is None:
        access_token = get_tenant_access_token()

    url = f'https://open.feishu.cn/open-apis/im/v1/messages/{message_id}'
    body = {
        "msg_type": "interactive",
        "content": json.dumps(card_content, ensure_ascii=False)
    }
    res = requests.patch(url, headers=get_headers(access_token), json=body)
    return res.json()


def reply_card_message(message_id: str, card_content: dict, access_token=None) -> dict:
    """
    回复消息（卡片形式）

    Args:
        message_id: 被回复的消息 ID
        card_content: 卡片内容
        access_token: 访问令牌（可选）

    Returns:
        dict: API 响应
    """
    if access_token is None:
        access_token = get_tenant_access_token()

    url = f'https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply'

    body = {
        "msg_type": "interactive",
        "content": json.dumps(card_content, ensure_ascii=False),
        'uuid': str(datetime.datetime.now().timestamp())
    }
    res = requests.post(url, headers=get_headers(access_token), json=body)
    return res.json()


# ==================== 消息分块常量 ====================
# 飞书消息长度限制
FEISHU_TEXT_MAX_LENGTH = 30000      # 文本消息约 30KB
FEISHU_CARD_MD_MAX_LENGTH = 10000   # 卡片 Markdown 单元素约 10KB
FEISHU_CARD_TOTAL_MAX_LENGTH = 80000  # 卡片总 JSON 约 100KB，保守取 80KB


def split_long_message(text: str, max_length: int = FEISHU_CARD_MD_MAX_LENGTH) -> list[str]:
    """
    将长消息分割为多个片段

    优先在自然边界（空行、换行）处分割，保持消息可读性。

    Args:
        text: 原始消息文本
        max_length: 每段最大长度

    Returns:
        list[str]: 分割后的消息片段列表
    """
    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # 在 max_length 附近寻找最佳分割点
        # 优先级：双换行 > 单换行 > 空格 > 强制截断
        search_start = max(0, max_length - 500)  # 在 max_length 前 500 字符内寻找分割点
        search_end = min(max_length, len(remaining))  # 不超过实际长度
        search_text = remaining[search_start:search_end]

        best_split = -1

        # 1. 优先在双换行（段落边界）分割
        double_newline = search_text.rfind('\n\n')
        if double_newline != -1:
            best_split = search_start + double_newline + 2
        else:
            # 2. 在单换行分割
            single_newline = search_text.rfind('\n')
            if single_newline != -1:
                best_split = search_start + single_newline + 1
            else:
                # 3. 在空格分割
                space = search_text.rfind(' ')
                if space != -1:
                    best_split = search_start + space + 1
                else:
                    # 4. 强制截断
                    best_split = max_length

        # 确保分割点不超过 max_length
        if best_split > max_length:
            best_split = max_length

        chunk = remaining[:best_split].strip()
        if chunk:
            chunks.append(chunk)

        # 更新剩余文本，如果分割点等于剩余长度则结束
        if best_split >= len(remaining):
            break
        remaining = remaining[best_split:].strip()

    return chunks


def send_long_message(
    receive_id: str,
    text: str,
    title: str = "",
    use_card: bool = True,
    access_token=None
) -> list[dict]:
    """
    发送长消息，自动分块

    当消息超过飞书限制时，自动分割为多条消息发送。

    Args:
        receive_id: 接收者 ID (chat_id)
        text: 消息内容
        title: 可选的卡片标题（仅第一条消息使用）
        use_card: 是否使用卡片模式
        access_token: 访问令牌（可选）

    Returns:
        list[dict]: 所有消息的 API 响应列表
    """
    max_length = FEISHU_CARD_MD_MAX_LENGTH if use_card else FEISHU_TEXT_MAX_LENGTH
    chunks = split_long_message(text, max_length)

    responses = []
    total = len(chunks)

    for i, chunk in enumerate(chunks):
        if use_card:
            from src.feishu_utils.card_builder import CardBuilder

            builder = CardBuilder()

            # 只有第一条消息带标题
            if i == 0 and title:
                builder.set_header(title, "blue")

            # 添加分页提示（如果有多条）
            if total > 1:
                chunk_text = f"{chunk}\n\n---\n📄 {i + 1}/{total}"
            else:
                chunk_text = chunk

            builder.add_div(chunk_text, "lark_md")
            card = builder.build()

            res = send_card_message(receive_id, card, access_token)
        else:
            # 文本模式
            if total > 1:
                chunk_text = f"{chunk}\n\n---\n📄 {i + 1}/{total}"
            else:
                chunk_text = chunk
            res = send_message(receive_id, chunk_text, access_token)

        responses.append(res)

    return responses


def send_long_markdown_message(
    receive_id: str,
    text: str,
    title: str = "",
    access_token=None
) -> list[dict]:
    """
    发送长 Markdown 消息（使用卡片渲染），自动分块

    Args:
        receive_id: 接收者 ID (chat_id)
        text: Markdown 内容
        title: 可选的卡片标题
        access_token: 访问令牌（可选）

    Returns:
        list[dict]: 所有消息的 API 响应列表
    """
    return send_long_message(receive_id, text, title, use_card=True, access_token=access_token)