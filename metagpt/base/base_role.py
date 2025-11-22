from abc import abstractmethod
from typing import Optional, Union

from metagpt.base.base_serialization import BaseSerialization


class BaseRole(BaseSerialization):
    """Abstract base class for all roles."""

    name: str

    @property
    def is_idle(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def think(self):
        """Consider what to do and decide on the next course of action."""
        raise NotImplementedError

    @abstractmethod
    def act(self):
        """Perform the current action."""
        raise NotImplementedError

    @abstractmethod
    async def react(self) -> "Message":
        """Entry to one of three strategies by which Role reacts to the observed Message."""

    @abstractmethod
    async def run(self, with_message: Optional[Union[str, "Message", list[str]]] = None) -> Optional["Message"]:
        """Observe, and think and act based on the results of the observation."""

    @abstractmethod
    def get_memories(self, k: int = 0) -> list["Message"]:
        """Return the most recent k memories of this role."""

    # yswang add
    def after_properties_set(self):
        """
        子类负责实现, 用于在Role的各种属性设置完毕后调用。
        主要用于 `Environment.add_roles()` 下role设置context和env之后的调用;
        子类可以重载这个方法用于设置 `chat_id`等
        """
