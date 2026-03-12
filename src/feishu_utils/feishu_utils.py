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