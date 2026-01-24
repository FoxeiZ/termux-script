from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    name: str
    module_path: str
    class_name: str
    requires_root: bool
    restart_on_failure: bool
    args: list[Any] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)
    webhook_url: str = ""
