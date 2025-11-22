from pydantic import BaseModel
from typing import List, Union, Any

class MentionTrigger(BaseModel):
    char: str
    id: str
    value: str

class InsertItem(BaseModel):
    # insert 可能是字符串或包含 mentiontrigger 的对象
    insert: Union[str, dict[str, MentionTrigger]]

class ChatMessage(BaseModel):
    content: List[InsertItem]
    type: str
    metadata: dict

    def is_empty(self):
        return self.content is None or len(self.content) == 0

# 统 Restful-API 响应体
class ResponseBody(BaseModel):
    code: int = 0
    message: str = "成功"
    data: Any = None

    @staticmethod
    def ok(data: Any):
        return ResponseBody(code=0, message="成功", data=data)

