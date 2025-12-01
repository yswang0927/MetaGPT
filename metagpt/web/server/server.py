from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import socketio
import asyncio
import json
import os
from uuid import uuid4
from datetime import datetime
from typing import Dict, Any

from metagpt import software_company
from models import ResponseBody, ChatMessage
from metagpt.chat.communication import CLIENT_WEBSOCKETS, CLIENT_MSG_QUEUE, format_output_message
from metagpt.const import DEFAULT_WORKSPACE_ROOT
from metagpt.logs import logger


# 后台任务：处理队列中的消息
async def websocket_message_sender():
    print("%%%%% start listen websocket_message_sender %%%%%%")
    while True:
        try:
            client_id, message = await CLIENT_MSG_QUEUE.get()
            if not client_id:
                continue
            formatted_msg = format_output_message(message)
            if formatted_msg is None:
                continue

            #if not isinstance(formatted_msg, str):
            #    formatted_msg = json.dumps(formatted_msg, ensure_ascii=False)
            msg_name, msg_content = formatted_msg # e.g. (msg:create, {...})
            await send_to_chat_id(client_id, msg_name, msg_content)
            CLIENT_MSG_QUEUE.task_done()
        except Exception as e:
            print(f">> Error: CLIENT_MSG_QUEUE error: {e} client_id={client_id}")
            logger.error(e)


# 在 lifespan 中启动后台任务
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动消息发送器
    sender_task = asyncio.create_task(websocket_message_sender())
    yield
    # 关闭时取消任务
    sender_task.cancel()


background_tasks = set()

# Create a Socket.IO server
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins='*'  # Configure this properly in production
)

# Create FastAPI app
app = FastAPI(lifespan=lifespan)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure this properly in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 存储 chat_id 和 sid 的映射关系
chat_id_to_sid: Dict[str, str] = {}
sid_to_chat_id: Dict[str, str] = {}

# Wrap with ASGI app
socket_app = socketio.ASGIApp(sio, app, socketio_path='/socket.io')

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# FastAPI routes
@app.get("/")
async def read_root():
    return {"message": "FastAPI + Socket.IO server running"}

@app.get("/api/status")
async def get_status():
    return {"status": "online", "connections": len(sio.manager.rooms.get('/', {}))}

@app.get("/chat/{chat_id}")
async def home(request: Request, chat_id: str):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "name": "World", "clientId": chat_id}
    )

@app.post("/api/v1/chats/{chat_id}/messages")
async def post_chat(chat_id: str, chatMessage: ChatMessage):
    """
    :param chat_id:
    :param data: {content:[{insert:"hello"}], "type": "message"}
    :return:
    """
    project_path = os.path.join(DEFAULT_WORKSPACE_ROOT, "chats", chat_id)
    if not os.path.exists(project_path):
        os.makedirs(project_path, exist_ok=True)

    """
    content:[{insert:"hello"}]
    @多人
    "content":[
        {"insert": {"mentiontrigger": {"char": "@","id": "Data Analyst","value": "David"}}},
        {"insert": " 分析下诗人李白的诗歌风格是什么样的？\n"}
        {"insert": {"mentiontrigger": {"char": "@","id": "Product Manager","value": "Emma"}}},
        {"insert": " 分析下诗人杜甫的诗歌风格是什么样的？\n"}
    ]
    """

    idea = ""
    if chatMessage.is_empty():
        raise HTTPException(status_code=400, detail="无效的请求参数")

    # TODO 处理 @多人的情况
    for insert_item in chatMessage.content:
        if isinstance(insert_item.insert, str):
            idea = idea + (insert_item.insert + "\n")

    # 创建后台任务
    task = asyncio.create_task(software_company.generate_repo2(
        chat_id,
        idea,
        10000,
        20,
        False,
        False,
        True,
        "project_test",
        False,
        project_path,
        "",
        1,
        None,
    ))
    # 防止任务被垃圾回收（重要！）
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)

    response_data = {
        "id": uuid4().hex,
        "chat_id": chat_id,
        "created_at": datetime.now(),
        "role": "User",
        "type": "message",
        "refer_id": None,
        "content": chatMessage,
        "reply_messages": None,
        "version": None,
        "uuid": None,
        "action_datas": None
    }
    return ResponseBody.ok(response_data)


# Socket.IO event handlers
@sio.event
async def connect(sid, environ):
    # 从查询参数中获取 chat_id
    query_string = environ.get('QUERY_STRING', '')
    params = dict(param.split('=') for param in query_string.split('&') if '=' in param)
    chat_id = params.get('chat_id')

    if chat_id:
        # 存储映射关系
        chat_id_to_sid[chat_id] = sid
        sid_to_chat_id[sid] = chat_id
        print(f"Client connected: {sid}, chat_id: {chat_id}")
        await sio.emit('message', {
            'data': f'Connected to server with chat_id: {chat_id}'
        }, room=sid)
    else:
        print(f"Client connected: {sid}, no chat_id provided")
        await sio.emit('message', {
            'data': 'Connected to server (no chat_id)'
        }, room=sid)

@sio.event
async def disconnect(sid):
    """客户端断开连接时清理映射关系"""
    chat_id = sid_to_chat_id.get(sid)
    if chat_id:
        del chat_id_to_sid[chat_id]
        del sid_to_chat_id[sid]
        print(f"Client disconnected: {sid}, chat_id: {chat_id}")
    else:
        print(f"Client disconnected: {sid}")

@sio.event
async def message(sid, data):
    print(f"Message from {sid}: {data}")
    # Echo the message back to the client
    await sio.emit('message', {'data': f"Server received: {data}"}, room=sid)

@sio.event
async def broadcast(sid, data):
    print(f"Broadcasting message from {sid}: {data}")
    # Broadcast to all connected clients
    await sio.emit('broadcast', {'data': data, 'from': sid})

@sio.event
async def join_room(sid, data):
    room = data.get('room')
    await sio.enter_room(sid, room)
    await sio.emit('message', {'data': f'Joined room: {room}'}, room=sid)
    await sio.emit('user_joined', {'user': sid}, room=room, skip_sid=sid)

@sio.event
async def leave_room(sid, data):
    room = data.get('room')
    await sio.leave_room(sid, room)
    await sio.emit('message', {'data': f'Left room: {room}'}, room=sid)
    await sio.emit('user_left', {'user': sid}, room=room)

@sio.event
async def room_message(sid, data):
    room = data.get('room')
    message = data.get('message')
    await sio.emit('room_message', {'data': message, 'from': sid}, room=room)

# 辅助函数：向指定 chat_id 发送消息
async def send_to_chat_id(chat_id: str, event: str, data: Any):
    """向指定的 chat_id 发送消息"""
    if chat_id in chat_id_to_sid:
        sid = chat_id_to_sid[chat_id]
        await sio.emit(event, data, room=sid)
        return True
    return False

# Run with: uvicorn main:socket_app --reload
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(socket_app, host="0.0.0.0", port=8000)