#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
和界面等通信相关的
@Author yswang
"""
from contextvars import ContextVar
from asyncio import Queue
from typing import Any
from datetime import datetime
from uuid import uuid4

# 实时存储当前正在工作的角色信息
CURRENT_ROLE: ContextVar[Any] = ContextVar("current_role")

# 存储当前的对话ID
CURRENT_CHAT_ID: ContextVar[str] = ContextVar("current_chat_id")

# 消息队列，接收 `report.py` 推送的消息，被 websocket 进行消费
# 数据格式：(client_id, msg)
CLIENT_MSG_QUEUE = Queue()

# 存储客户端ID 和 websocket 对象的映射关系，便于推送消息
# 数据格式：{client_id: websocket}
CLIENT_WEBSOCKETS = dict()

# 维护人名和角色定义
ROLES_PROFILE = {
    "Bob": "Architect",
    "Eve": "Project Manager",
    "Alice": "Product Manager",
    "Alex": "Engineer",
    "Edward": "QaEngineer",
    "Mike": "Team Leader",
    "David": "DataAnalyst",
    "John Smith": "Retail Sales Guide"
}

def extract_block_data(report_msg):
    block = report_msg.get('block', None)
    uuid = str(report_msg.get('uuid', ""))
    value = report_msg.get('value', "")
    name = report_msg.get('name', "")
    chat_id = report_msg.get('chat_id', "")
    role = report_msg.get('role', "")
    value_type = report_msg.get('type', "")
    content_index = report_msg.get('content_index', 0)
    return block, uuid, name, value, value_type, role, chat_id, content_index

# 将 Block 消息格式转换为前端需要的格式
def format_output_message(report_msg):
    """
    将report.py推送的消息体转换为前端页面需要的格式
    {'block': '', 'uuid': '', 'value': , 'name': '', 'role': '', 'chat_id': '', 'type':''}
    """
    if not report_msg:
        return None
    if isinstance(report_msg, str):
        return report_msg
    if not isinstance(report_msg, dict):
        return None

    (block, uuid, name, value, value_type, role, chat_id, content_index) = extract_block_data(report_msg)
    if not block or not chat_id:
        return None

    # 转换 block=Event
    if "Event" == block:
        return format_block_event(report_msg)

    # 转换 block=Thought
    if "Thought" == block:
        return format_block_thought(report_msg)

    # 转换 block=Command
    if "Command" == block:
        return format_block_command(report_msg)

    # 转换 block=Task
    if "Task" == block:
        return format_block_task(report_msg)

    # 转换 block=Editor
    if "Editor" == block:
        return format_block_editor(report_msg)

    # 转换 block=Terminal
    if "Terminal" == block:
        return format_block_terminal(report_msg)

    return report_msg


# 处理 block=Event 消息格式
def format_block_event(report_msg):
    (block, uuid, name, value, value_type, role, chat_id, content_index) = extract_block_data(report_msg)
    msg = {
        "chat_id": chat_id,
        "metadata": value
    }
    return ("chat:update", msg)

# 处理 block=Thought 消息格式
def format_block_thought(report_msg):
    (block, uuid, name, value, value_type, role, chat_id, content_index) = extract_block_data(report_msg)

    # 意图识别跳过(?)：
    if 'classify' == value_type:
        return None

    msg_type = "think" if "react" == value_type else "message"

    # msg:create
    if 'object' == name and ('react' == value_type or 'quick' == value_type):
        # msg:create消息格式
        msg = {
            "chat_id": chat_id,
            "id": uuid,
            "uuid": uuid,
            "role": role,
            "type": msg_type,
            "refer_id": "0",
            "content": [],
            "reply_messages": None,
            "action_datas": None,
            "created_at": datetime.now().isoformat(),
        }
        return ("msg:create", msg)

    # msg:update
    if 'content' == name:
        # msg:update 消息格式
        msg = {
            "chat_id": chat_id,
            "id": uuid,
            "message_uuid": uuid,
            "content": [],
            "type": msg_type,
            "is_finished": False,
            "content_index": content_index
        }
        if isinstance(value, str):
            msg["content"].append({"insert": value})
        elif isinstance(value, dict) and "content" in value:
            # 从 `TeamLeader.publish_message` 发送的消息
            msg["content"].append({"insert": value.get("content", "")})
            if "send_to" in value:
                send_to = value.get("send_to", "")
                if send_to:
                    msg["content"].append({"insert": {
                        "mentiontrigger": {"char": "@", "id": ROLES_PROFILE.get(send_to, "unknown"), "value": send_to}
                    }})
        return ("msg:update", msg)

    # finished
    if 'end_marker' == name:
        # msg:update is_finished=True 格式
        msg = {
            "chat_id": chat_id,
            "id": uuid,
            "uuid": uuid,
            "content": [],
            "is_finished": True
        }
        if value is not None:
            msg["content"].append({"insert": value})
        return ("msg:update", msg)

    return None

# 处理 block=Task 消息格式
def format_block_task(report_msg):
    """
    { 'block': 'Task', 'uuid': '61d23c4', 
        'value': {
            'tasks': [
                {'task_id': '1', 'dependent_task_ids': [], 'instruction': '任务1描述...', 'task_type': '', 'code': '',
                  'result': '', 'is_success': False, 'is_finished': True, 'assignee': 'Alex'}
            ], 
            'current_task_id': ''
        }, 
        'name': 'object', 
        'role': 'Mike(Team Leader)', 
        'chat_id': 'c001'
    }
    输出格式：
    ["chat:update",{"chat_id":"2a389dbf27fd42249fff46509a94769e",
	    "metadata":{
	        "state":0,"status":"running","activities":[
                {"role":"Engineer","event":"开发一个...","type":"working"},
                {"role":"Team Leader","event":"thinking","type":"thinking"}
        ]}}
    ]
    """
    (block, uuid, name, value, value_type, role, chat_id, content_index) = extract_block_data(report_msg)
    tasks = value.get("tasks", [])
    if not isinstance(tasks, list) or len(tasks) == 0:
        return None

    activities = []
    for task in tasks:
        activities.append({
            "event": task.get("instruction", ""),
            "role": task.get("assignee", ""),
            "type": "working"
        })

    activities.append({
        "role": role,
        "event": "thinking",
        "type": "thinking"
    })

    msg = {
        "chat_id": chat_id,
        "metadata": {
            "state": 0,
            "status": "running",
            "activities": activities
        }
    }
    return ("chat:update", msg)

# 处理 block-Command 消息格式
def format_block_command(report_msg):
    """
    from: `metagpt/strategy/experience_retriever.py`
    Plan.append_task, Plan.finish_current_task
    TeamLeader.publish_message
    RoleZero.reply_to_human
    Editor.create_file, Editor.open_file, Editor.edit_file_by_replace, Editor.insert_content_at_line, Editor.read, Editor.write
    Terminal.run_command

    所有命令执行的开始：
    {'block': 'Command', 'uuid': '38fd236ec9e0', 'message_uuid': '4fc311212d09',
        'value': {'type': 'command'}, 'name': 'object', 'role': 'Team Leader', 'chat_id': 'c001', 'type': 'command'}
    每个命令：
    {'block': 'Command', 'uuid': '38fd236ec9e0', 'message_uuid': '4fc311212d09',
        'value': {
            "status": "success|failed|not_found",
            'command_name': 'Plan.append_task',
            'args': {'task_id': '1', 'dependent_task_ids': [], 'instruction': '任务1',
            'assignee': 'Alex'}
        },
        'name': 'content', 'role': 'Team Leader', 'chat_id': 'c001', 'type': 'command'}
    {'block': 'Command', 'uuid': '38fd236ec9e0', 'message_uuid': '4fc311212d09',
        'value': {
            "status": "success|failed|not_found",
            'command_name': 'TeamLeader.publish_message',
            'args': {'content': '你好', 'send_to': 'Alex'}
        },
        'name': 'content', 'role': 'Team Leader', 'chat_id': 'c001', 'type': 'command'}
    {'block': 'Command', 'uuid': '38fd236ec9e0', 'message_uuid': '4fc311212d09',
        'value': {
            "status": "success|failed|not_found",
            'command_name': 'RoleZero.reply_to_human',
            'args': {'content': '哈喽'}
        },
        'name': 'content', 'role': 'Team Leader', 'chat_id': 'c001', 'type': 'command'}
    """
    (block, uuid, name, value, value_type, role, chat_id, content_index) = extract_block_data(report_msg)
    message_uuid = report_msg.get("message_uuid", "")

    action_block_types = {
        "plan_append_task": "Task",
        "plan_finish_current_task": "Task",
        "editor_write": "Editor",
        "editor_read": "Editor",
        "editor_open_file": "Editor",
        "editor_goto_line": "Editor",
        "editor_scroll_down": "Editor",
        "editor_scroll_up": "Editor",
        "editor_create_file": "Editor",
        "editor_edit_file_by_replace": "Editor",
        "editor_insert_content_at_line": "Editor",
        "editor_append_file": "Editor",
        "editor_search_dir": "Editor",
        "editor_search_file": "Editor",
        "editor_find_file": "Editor",
        "editor_similarity_search": "Editor",
        "terminal_run_command": "Terminal",
    }

    # 在推送 action_data:update 之前先推送：
    #  ["msg:update", {"id":4,"content":[{"action_data":{"uuid":"8f77"}}],"is_finished":false,"content_index":114}]
    if 'object' == name and 'command' == value_type:
        msg = {
            "chat_id": chat_id,
            "id": message_uuid,
            "content": [{"action_data": {"uuid": uuid}}],
            "role": role,
            "is_finished": False
        }
        return ("msg:update", msg)

    # 转换每个 command 命令：value: { 'command_name:'', args:{...} }
    if "content" == name:
        # 转换 command_name：Plan.append_task -> plan_append_task
        cmd_name = value.get("command_name", "").lower()
        cmd_name = cmd_name.replace('.', '_')
        args = value.get("args", None)

        # 特殊的命令处理
        # 对于 `TeamLeader.publish_message` 命令直接推送为普通消息
        if "teamleader_publish_message" == cmd_name:
            if not args or not isinstance(args, dict):
                return None
            cmd_uuid = uuid4().hex
            msg = {
                "chat_id": chat_id,
                "message_uuid": message_uuid,
                "id": cmd_uuid,
                "uuid": cmd_uuid,
                "content":[
                    {"insert": args.get("content", "") }
                ],
                "role": role,
                "type": "message",
                "is_finished": True
            }
            send_to = args.get("send_to", None)
            if send_to:
                msg["content"].append({"insert": {"mentiontrigger": {"char":"@", "id": ROLES_PROFILE.get(send_to, "unknown"), "value": send_to }}})
            return ("msg:update", msg)

        # 对于 `RoleZero.reply_to_human` 命令直接推送为普通消息
        if "rolezero_reply_to_human" == cmd_name:
            if not args or not isinstance(args, dict):
                return None
            cmd_uuid = uuid4().hex
            msg = {
                "chat_id": chat_id,
                "message_uuid": message_uuid,
                "id": cmd_uuid,
                "uuid": cmd_uuid,
                "type": "message",
                "role": role,
                "content": [{"insert": args.get("content", "")}]
            }
            return ("msg:update", msg)

        # 对于 `RoleZero.ask_human` 命令，要输出一个 msg:create 消息
        if "rolezero_ask_human" == cmd_name:
            if not args or not isinstance(args, dict):
                return None
            cmd_uuid = uuid4().hex
            msg = {
                "chat_id": chat_id,
                "message_uuid": message_uuid,
                "id": cmd_uuid,
                "uuid": cmd_uuid,
                "type": "task",
                "role": role,
                "refer_id": message_uuid,
                "content": {
                    "status": "todo",
                    "question": args.get("question", "")
                }
            }
            return ("msg:create", msg)

        # 其它的 command 都属于标准 action_data
        # ["action_data:update", {message_uuid, action_data: {block_type, command_name, status:'running|success', uuid, message_uuid, ...} }]
        msg = {
            "chat_id": chat_id,
            "message_uuid": message_uuid
        }
        action_data = {
            "block_type": action_block_types.get(cmd_name, "unknown"),
            "command_name": cmd_name,
            "uuid": uuid,
            "message_uuid": message_uuid,
            "status": value.get("status", None),
            "created_at": datetime.now().isoformat(),
            **args
        }
        msg["action_data"] = action_data
        return ("action_data:update", msg)

    if 'end_marker' == name:
        return None

    return None


# 处理 block=Editor 消息格式
def format_block_editor(report_msg):
    """
    输出格式1：name=path 创建文件
        {'block': 'Editor', 'uuid': 'ae8d3cd50db4', 'value': '/gobang_game_prd.md', 'name': 'path', 'role': 'Product Manager', 'chat_id': 'c001'}
    输出格式2：name=meta type=code, 实时输出文件内容
        {'block': 'Editor', 'uuid': 'a13e80145546', 'value': {'type': 'code', 'filename': 'index.html', 'src_path': '/workspace/index.html'},
            'name': 'meta', 'role': 'Engineer', 'chat_id': 'c001', 'type': 'code'}
    输出格式3：name=content type=code, 实时输出文件内容
        {'block': 'Editor', 'uuid': 'a13e80145546', 'value': 'DOCTYPE html', 'name': 'content', 'role': 'Engineer', 'chat_id': 'c001', 'type': 'code'}
    输出格式4：
        {'block': 'Editor', 'uuid': 'a13e80145546', 'value': None, 'name': 'end_marker', 'role': 'Engineer', 'chat_id': 'c001', 'type': 'code'}
    """
    (block, uuid, name, value, value_type, role, chat_id, content_index) = extract_block_data(report_msg)

    if "path" == name:
        """
        {'block': 'Editor', 'uuid': 'ae8d3cd50db4', 'value': '/gobang_game_prd.md', 'name': 'path', 'role': 'Product Manager', 'chat_id': 'c001'}
        """
        # TODO
        return None

    if "document" == name:
        # TODO
        return None

    if "meta" == name and isinstance(value, dict) and "src_path" in value:
        """
        {'block': 'Editor', 'uuid': 'a13e80145546', 'value': {'type': 'code', 'filename': 'index.html', 'src_path': '/workspace/index.html'}, 
        'name': 'meta', 'role': 'Engineer', 'chat_id': 'c001', 'type': 'code'}
        -->
            ["timeline:start", { "chat_id":"", "index":5, "block_type":"Editor", "block_owner":"Engineer",
                "file":".storage/5/f9a0fab2/todo.md", "src_path":"/workspace/todo.md",
                "content":"", "content_index":0, "extra_data":null, "version":"v1" }
            ]
        """
        msg = {
            "chat_id": chat_id,
            "uuid": uuid,
            "block_type": "Editor",
            "block_owner": role,
            "file": f".storage/{chat_id}/{value.get("filename", "unknown")}",
            "src_path": value.get("src_path"),
            "content": ""
        }
        return ("timeline:start", msg)

    # 实时输出文件内容
    if "content" == name and "code" == value_type:
        """
        {'block': 'Editor', 'uuid': 'a13e80145546', 'value': 'DOCTYPE html', 'name': 'content', 'role': 'Engineer', 'chat_id': 'c001', 'type': 'code'}
        -->
            ["timeline:content",{"chat_id":"","index":5,"block_type":"Editor","block_owner":"Engineer",
              "file":"","src_path":"", "content":"DOCTYPE html", "content_index":9,"extra_data":null,"version":null}]
        """
        msg = {
            "chat_id": chat_id,
            "uuid": uuid,
            "block_type": "Editor",
            "block_owner": role,
            "file": "",
            "src_path": "",
            "content": value
        }
        return ("timeline:content", msg)

    # 文件内容输出结束
    if "end_marker" == name and "code" == value_type:
        """
        {'block': 'Editor', 'uuid': 'a13e80145546', 'value': None, 'name': 'end_marker', 'role': 'Engineer', 'chat_id': 'c001', 'type': 'code'}
        -->
            ["timeline:complete", {"chat_id":"", "index":4, "block_type":"Editor", "block_owner":"Engineer",
                "file":".storage/index.html","src_path":"/workspace/index.html",
                "content":"", "content_index":0,"extra_data":null,"version":"v1"}]
        """
        msg = {
            "chat_id": chat_id,
            "uuid": uuid,
            "block_type": "Editor",
            "block_owner": role,
            "file": "",  # 原始消息没有
            "src_path": "",  # 原始消息没有
            "content": ""
        }
        return ("timeline:complete", msg)

    return None


def format_block_terminal(report_msg):
    """
    {'block': 'Terminal', 'uuid': 'e564ad5fedd1', 'role': None, 'value': 'pwd\n', 'name': 'cmd'}
    {'block': 'Terminal', 'uuid': 'e564ad5fedd1', 'role': None, 'value': '/workspace\n', 'name': 'output'}
    {'block': 'Terminal', 'uuid': 'e564ad5fedd1', 'role': None, 'value': None, 'name': 'end_marker'}
    """
    (block, uuid, name, value, value_type, role, chat_id, content_index) = extract_block_data(report_msg)
    msg = {
        "chat_id": chat_id,
        "message_uuid": uuid,
        "uuid": uuid,
        "block_type": "Terminal",
        "content": ""
    }
    if 'cmd' == name:
        """这是执行的命令内容"""
        msg["content"] = value
        return ("timeline:start", msg)
    elif 'output' == name:
        """这是命令的输出内容"""
        msg["content"] = value
        return ("timeline:content", msg)
    elif "end_marker" == name:
        """命令执行结束"""
        return ("timeline:complete", msg)
    return None
