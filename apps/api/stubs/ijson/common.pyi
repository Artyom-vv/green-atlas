"""Validated record assembly exposed by the installed ijson package."""

from collections.abc import Callable
from typing import Any

class ObjectBuilder:
    value: Any
    def __init__(self, map_type: Callable[[], dict] | None = ...) -> None: ...
    def event(self, event: str, value: Any) -> None: ...
