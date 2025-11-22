from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import json
import os
from uuid import uuid4
from datetime import datetime

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
            #print(f"<< formatted: {formatted_msg}")
            if formatted_msg is None:
                continue
            ws = CLIENT_WEBSOCKETS.get(client_id, None)
            if ws:
                try:
                    if not isinstance(formatted_msg, str):
                        formatted_msg = json.dumps(formatted_msg, ensure_ascii=False)
                    await ws.send_text(formatted_msg)
                except Exception as e:
                    print(f">> Error: Send msg to Client<{client_id}> failed: {e}")
                    CLIENT_WEBSOCKETS.pop(client_id, None)
            else:
                print(f">> Warning: Not found Client<{client_id}>'s websocket.")
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

app = FastAPI(lifespan=lifespan)

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/chat/{chat_id}")
async def home(request: Request, chat_id: str):
    return templates.TemplateResponse("index.html",
                                      {"request": request, "name": "World",
                                       "clientId": chat_id})


@app.post("/api/v1/chats")
async def new_chat():
    return ResponseBody.ok({
        "chat_id": uuid4().hex,
        "title": "新对话"
    })

@app.get("/api/v1/chats")
async def new_chat():
    return ResponseBody.ok([])

@app.patch("/api/v1/chats/{chat_id}")
async def stop_chat(data:dict):
    return ResponseBody.ok([])

@app.get("/api/v1/chats/{chat_id}/messages")
async def get_history_messages():
    return ResponseBody.ok([])

@app.post("/api/v1/chats/{chat_id}/messages")
async def post_chat(chat_id: str, chatMessage: ChatMessage):
    """
    :param chat_id:
    :param data: {content:[{insert:"hello"}], "type": "message"}
    :return:
    """

    project_path = os.path.join(DEFAULT_WORKSPACE_ROOT, "yswang", "chats")
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


@app.get("/demo/{item_id}")
async def demo(
    request: Request,
    item_id: int,  # 路径参数
    q: str = None  # 查询参数
):
    return {
        "item_id_from_path": item_id,
        "q_from_query": q,
        "path_params": request.path_params,  # {'item_id': '123'}
        "query_params": dict(request.query_params),  # {'q': 'hello'}
        "user_agent": request.headers.get("user-agent"),
        "client_ip": request.client.host if request.client else "unknown",
        "full_url": str(request.url),
        "cookies": request.cookies,
    }


""" =================== Websocket服务 =================== """

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await websocket.accept()
    CLIENT_WEBSOCKETS[client_id] = websocket
    print(f">> Client <{client_id}> connected. Total: {len(CLIENT_WEBSOCKETS)}")
    #last_heartbeat = asyncio.get_event_loop().time()

    try:
        while True:
            try:
                # 保持连接（即使不需要处理消息）
                #data = await websocket.receive_text()
                # 等待客户端消息，超时 30 秒
                data = await asyncio.wait_for(websocket.receive_text(), timeout=600)
                # 心跳检测
                if '2' == data:
                    await websocket.send_text("3")
                    #print(f">> Heartbeat from Client <{client_id}>: received '2', replied '3'")
            except asyncio.TimeoutError:
                # 30秒无消息，主动断开
                print(f">> Warning: Client <{client_id}> heartbeat timeout")
                break
    except WebSocketDisconnect:
        pass
    finally:
        CLIENT_WEBSOCKETS.pop(client_id, None)
        print(f">> Warning: Client <{client_id}> disconnected")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
