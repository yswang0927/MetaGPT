import asyncio
import os
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Literal, Optional, Union
from uuid import UUID, uuid4

from playwright.async_api import Page as AsyncPage
from playwright.sync_api import Page as SyncPage
from pydantic import BaseModel, Field, PrivateAttr

from metagpt.const import METAGPT_REPORTER_DEFAULT_URL
from metagpt.logs import create_llm_stream_queue, get_llm_stream_queue

try:
    import requests_unixsocket as requests
except ImportError:
    import requests

# yswang add
from metagpt.chat.communication import (
    CLIENT_MSG_QUEUE,
    StreamingRemoveCodeBlockFilter
)


class BlockType(str, Enum):
    """Enumeration for different types of blocks."""
    TERMINAL = "Terminal"
    TASK = "Task"
    BROWSER = "Browser"
    BROWSER_RT = "Browser-RT"
    EDITOR = "Editor"
    GALLERY = "Gallery"
    NOTEBOOK = "Notebook"
    DOCS = "Docs"
    THOUGHT = "Thought"
    # yswang add
    EVENT = "Event"
    COMMAND = "Command"


END_MARKER_NAME = "end_marker"
END_MARKER_VALUE = "\x18\x19\x1B\x18\n"


class ResourceReporter(BaseModel):
    """Base class for resource reporting."""

    block: BlockType = Field(description="The type of block that is reporting the resource")
    uuid: UUID = Field(default_factory=uuid4, description="The unique identifier for the resource")
    enable_llm_stream: bool = Field(False, description="Indicates whether to connect to an LLM stream for reporting")
    callback_url: str = Field(METAGPT_REPORTER_DEFAULT_URL, description="The URL to which the report should be sent")
    _llm_task: Optional[asyncio.Task] = PrivateAttr(None)
    # yswang add
    chat_id: str = Field("", description="The chat_id that is reporting the resource", exclude=True)
    # role 不能参与主动序列化，否则会引起循环依赖，因为在 Role会使用 Reporter
    role: Any = Field(None, description="The role that is reporting the resource", exclude=True)
    _value_type: str = PrivateAttr("") # 增加 _value_type 用于记录首次发送的 value: {type:'xxx'}
    _content_index: int = PrivateAttr(0)

    # yswang add
    def get_id(self):
        return str(self.uuid.hex)

    def get_uuid(self):
        return str(self.uuid.hex)

    def set_chat_id(self, chat_id: str):
        self.chat_id = chat_id

    def set_role(self, role: Any):
        self.role = role


    def report(self, value: Any, name: str, extra: Optional[dict] = None):
        """Synchronously report resource observation data.

        Args:
            value: The data to report.
            name: The type name of the data.
        """
        return self._report(value, name, extra)

    async def async_report(self, value: Any, name: str, extra: Optional[dict] = None):
        """Asynchronously report resource observation data.

        Args:
            value: The data to report.
            name: The type name of the data.
        """
        return await self._async_report(value, name, extra)

    @classmethod
    def set_report_fn(cls, fn: Callable):
        """Set the synchronous report function.

        Args:
            fn: A callable function used for synchronous reporting. For example:

                >>> def _report(self, value: Any, name: str):
                ...     print(value, name)

        """
        cls._report = fn

    @classmethod
    def set_async_report_fn(cls, fn: Callable):
        """Set the asynchronous report function.

        Args:
            fn: A callable function used for asynchronous reporting. For example:

                ```python
                >>> async def _report(self, value: Any, name: str):
                ...     print(value, name)
                ```
        """
        cls._async_report = fn

    def _report(self, value: Any, name: str, extra: Optional[dict] = None):
        data = self._format_data(value, name, extra)
        print(f">> {self.block} _report >> {data}")

        # yswang add 向消息队列推送消息，便于显示在界面上
        CLIENT_MSG_QUEUE.put_nowait((self.chat_id, data))

        """
        if not self.callback_url:
            return
        resp = requests.post(self.callback_url, json=data)
        resp.raise_for_status()
        return resp.text
        """

    async def _async_report(self, value: Any, name: str, extra: Optional[dict] = None):
        data = self._format_data(value, name, extra)
        print(f">> {self.block} _async_report>>> {data}")

        # yswang add 向消息队列推送消息，便于显示在界面上
        CLIENT_MSG_QUEUE.put_nowait((self.chat_id, data))

        """
        if not self.callback_url:
            return
        url = self.callback_url
        _result = urlparse(url)
        sessiion_kwargs = {}
        if _result.scheme.endswith("+unix"):
            parsed_list = list(_result)
            parsed_list[0] = parsed_list[0][:-5]
            parsed_list[1] = "fake.org"
            url = urlunparse(parsed_list)
            sessiion_kwargs["connector"] = UnixConnector(path=unquote(_result.netloc))

        async with ClientSession(**sessiion_kwargs) as client:
            async with client.post(url, json=data) as resp:
                resp.raise_for_status()
                return await resp.text()
        """

    def _format_data(self, value, name, extra):
        self._content_index += 1
        data = self.model_dump(mode="json", exclude=("callback_url", "llm_stream", "enable_llm_stream", "role"))

        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        elif isinstance(value, Path):
            value = str(value)

        # yswang add 读取 type 值
        if isinstance(value, dict) and ("type" in value):
            self._value_type = value.get("type", None)

        if name == "path":
            value = os.path.abspath(value)
        data["value"] = value
        data["name"] = name

        if self.role is not None:
            # yswang modify
            data["role"] = self.role.profile
            data["role_name"] = self.role.name
        else:
            #role_name = os.environ.get("METAGPT_ROLE")
            data["role"] = ''
            data["role_name"] = ''

        if extra:
            data["extra"] = extra

        # yswang add
        data["chat_id"] = self.chat_id
        if self._value_type:
            data["type"] = self._value_type
        data["content_index"] = self._content_index

        return data

    def __enter__(self):
        """Enter the synchronous streaming callback context."""
        return self

    def __exit__(self, *args, **kwargs):
        """Exit the synchronous streaming callback context."""
        self.report(None, END_MARKER_NAME)

    async def __aenter__(self):
        """Enter the asynchronous streaming callback context."""
        if self.enable_llm_stream:
            # yswang note: 这边调用 `logs.py#create_llm_stream_queue()`方法创建流式队列，
            # 在 `openai_api.py` 等provider的 `_achat_completion_stream()` 中流式输出的
            # 每个消息都会进入这个队列中，然后被异步消费
            queue = create_llm_stream_queue()
            self._llm_task = asyncio.create_task(self._llm_stream_report(queue))
        return self

    async def __aexit__(self, exc_type, exc_value, exc_tb):
        """Exit the asynchronous streaming callback context."""
        if self.enable_llm_stream and exc_type != asyncio.CancelledError:
            await get_llm_stream_queue().put(None)
            await self._llm_task
            self._llm_task = None
        await self.async_report(None, END_MARKER_NAME)

    async def _llm_stream_report(self, queue: asyncio.Queue):
        while True:
            data = await queue.get()
            if data is None:
                return
            await self.async_report(data, "content")

    async def wait_llm_stream_report(self):
        """Wait for the LLM stream report to complete."""
        queue = get_llm_stream_queue()
        while self._llm_task:
            if queue.empty():
                break
            await asyncio.sleep(0.1) #0.01


class TerminalReporter(ResourceReporter):
    """Terminal output callback for streaming reporting of command and output.

    The terminal has state, and an agent can open multiple terminals and input different commands into them.
    To correctly display these states, each terminal should have its own unique ID, so in practice, each terminal
    should instantiate its own TerminalReporter object.
    """

    block: Literal[BlockType.TERMINAL] = BlockType.TERMINAL

    def report(self, value: str, name: Literal["cmd", "output"]):
        """Report terminal command or output synchronously."""
        return super().report(value, name)

    async def async_report(self, value: str, name: Literal["cmd", "output"]):
        """Report terminal command or output asynchronously."""
        return await super().async_report(value, name)


class BrowserReporter(ResourceReporter):
    """Browser output callback for streaming reporting of requested URL and page content.

    The browser has state, so in practice, each browser should instantiate its own BrowserReporter object.
    """

    block: Literal[BlockType.BROWSER] = BlockType.BROWSER

    def report(self, value: Union[str, SyncPage], name: Literal["url", "page"]):
        """Report browser URL or page content synchronously."""
        if name == "page":
            value = {"page_url": value.url, "title": value.title(), "screenshot": str(value.screenshot())}
        return super().report(value, name)

    async def async_report(self, value: Union[str, AsyncPage], name: Literal["url", "page"]):
        """Report browser URL or page content asynchronously."""
        if name == "page":
            value = {"page_url": value.url, "title": await value.title(), "screenshot": str(await value.screenshot())}
        return await super().async_report(value, name)


class ServerReporter(ResourceReporter):
    """Callback for server deployment reporting."""

    block: Literal[BlockType.BROWSER_RT] = BlockType.BROWSER_RT

    def report(self, value: str, name: Literal["local_url"] = "local_url"):
        """Report server deployment synchronously."""
        return super().report(value, name)

    async def async_report(self, value: str, name: Literal["local_url"] = "local_url"):
        """Report server deployment asynchronously."""
        return await super().async_report(value, name)


class ObjectReporter(ResourceReporter):
    """Callback for reporting complete object resources."""

    def report(self, value: dict, name: Literal["object"] = "object"):
        """Report object resource synchronously."""
        return super().report(value, name)

    async def async_report(self, value: dict, name: Literal["object"] = "object"):
        """Report object resource asynchronously."""
        return await super().async_report(value, name)


class TaskReporter(ObjectReporter):
    """Reporter for object resources to Task Block."""

    block: Literal[BlockType.TASK] = BlockType.TASK


class ThoughtReporter(ObjectReporter):
    """Reporter for object resources to Task Block."""
    block: Literal[BlockType.THOUGHT] = BlockType.THOUGHT
    # yswang add 过滤掉不需要的command代码块
    _cmd_block_filter: StreamingRemoveCodeBlockFilter = StreamingRemoveCodeBlockFilter()
    # yswang add 存储完整内容
    _finish_content: list = list()

    def report(self, value: dict, name: Literal["object"] = "object"):
        """Report object resource synchronously."""
        return super().report(value, name)

    # yswang add 支持过滤掉思考中输出的 ```json ``` 命令
    async def async_report(self, value: Any, name: Literal["object"] = "object"):
        if not isinstance(value, str):
            return await super().async_report(value, name)

        filtered = self._cmd_block_filter.filter_chunk(value)
        if filtered:
            self._finish_content.append(filtered)
            #print(f"\033[34m{filtered}\033[0m", end="")
            return await super().async_report(filtered, name)
        return None

    async def __aexit__(self, exc_type, exc_value, exc_tb):
        """Exit the asynchronous streaming callback context."""
        if self.enable_llm_stream and exc_type != asyncio.CancelledError:
            await get_llm_stream_queue().put(None)
            await self._llm_task
            self._llm_task = None
            # 输出最后可能剩余的部分内容
            remain_content = self._cmd_block_filter.finalize()
            self._finish_content.append(remain_content)
            await super().async_report(remain_content, name="content")
            #print("\033[34m~~~~~~~async end~~~~~~~~~\033[0m")
        # yswang modify 最后输出完整内容
        await super().async_report("".join(self._finish_content) , END_MARKER_NAME)


class FileReporter(ResourceReporter):
    """File resource callback for reporting complete file paths.

    There are two scenarios: if the file needs to be output in its entirety at once, use non-streaming callback;
    if the file can be partially output for display first, use streaming callback.
    """

    def report(
        self,
        value: Union[Path, dict, Any],
        name: Literal["path", "meta", "content"] = "path",
        extra: Optional[dict] = None,
    ):
        """Report file resource synchronously."""
        return super().report(value, name, extra)

    async def async_report(
        self,
        value: Union[Path, dict, Any],
        name: Literal["path", "meta", "content"] = "path",
        extra: Optional[dict] = None,
    ):
        """Report file resource asynchronously."""
        return await super().async_report(value, name, extra)


class NotebookReporter(FileReporter):
    """Equivalent to FileReporter(block=BlockType.NOTEBOOK)."""

    block: Literal[BlockType.NOTEBOOK] = BlockType.NOTEBOOK


class DocsReporter(FileReporter):
    """Equivalent to FileReporter(block=BlockType.DOCS)."""

    block: Literal[BlockType.DOCS] = BlockType.DOCS


class EditorReporter(FileReporter):
    """Equivalent to FileReporter(block=BlockType.EDITOR)."""

    block: Literal[BlockType.EDITOR] = BlockType.EDITOR


class GalleryReporter(FileReporter):
    """Image resource callback for reporting complete file paths.

    Since images need to be complete before display, each callback is a complete file path. However, the Gallery
    needs to display the type of image and prompt, so if there is meta information, it should be reported in a
    streaming manner.
    """

    block: Literal[BlockType.GALLERY] = BlockType.GALLERY

    def report(self, value: Union[dict, Path], name: Literal["meta", "path"] = "path"):
        """Report image resource synchronously."""
        return super().report(value, name)

    async def async_report(self, value: Union[dict, Path], name: Literal["meta", "path"] = "path"):
        """Report image resource asynchronously."""
        return await super().async_report(value, name)


# yswang add
class ChatEventReporter(ObjectReporter):
    """
    报告当前chat下的全局事件信息
    """
    block: Literal[BlockType.EVENT] = BlockType.EVENT

    def report_sleeping(self, state:int = 1, status: str = "sleeping"):
        return self.report({"state": state, "status": status, "activities": []})

    def report_activities(self, activities: list = []):
        return self.report({"state": 0, "status": "running", "activities": activities})

    def report_role_event(self, role: str = "", event: str = "thinking", type: str = "thinking"):
        return self.report_activities([{"role": role, "event": event, "type": type}])

    def report(self, value: Union[dict, str], name: Literal["chat"] = "chat"):
        return super().report(value, name)

    async def async_report(self, value: Union[dict, str], name: Literal["chat"] = "chat"):
        return await super().async_report(value, name)


class CommandReporter(ObjectReporter):
    block: Literal[BlockType.COMMAND] = BlockType.COMMAND
    message_uuid: str = ""  # 记录这些command来自哪个消息

