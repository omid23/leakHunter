"""Tool contracts: spec (name, description, strict JSON schema), the result envelope and a registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from leakhunter.domain.models import FATAL_TOOL_ERRORS, ErrorKind


class ToolError(Exception):
    """Raised by tool handlers for expected, classified failures."""

    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    result_model: type[BaseModel]

    def llm_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": strict_schema(self.args_model),
            "strict": True,
        }


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema with every object closed (``additionalProperties: false``) and every property required."""

    def close(node: Any) -> Any:
        if isinstance(node, dict):
            node = {k: close(v) for k, v in node.items() if k not in {"title", "default"}}
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            return node
        if isinstance(node, list):
            return [close(v) for v in node]
        return node

    result: dict[str, Any] = close(model.model_json_schema())
    return result


class Tool(Protocol):
    spec: ToolSpec

    async def run(self, args: Any) -> BaseModel: ...


class ToolResult(BaseModel):
    tool: str
    ok: bool
    data: dict[str, Any] | None = None
    error_kind: ErrorKind | None = None
    error_message: str | None = None
    attempts: int
    duration_ms: float

    @property
    def fatal(self) -> bool:
        return self.error_kind in FATAL_TOOL_ERRORS

    def for_model(self) -> dict[str, Any]:
        """What the LLM sees: data on success, a classified error otherwise."""
        if self.ok:
            return self.data or {}
        return {"error": {"kind": self.error_kind, "message": self.error_message, "attempts": self.attempts}}


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.spec.name in self._tools:
            raise ValueError(f"duplicate tool {tool.spec.name}")
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def definitions(self) -> list[dict[str, Any]]:
        return [tool.spec.llm_definition() for tool in self._tools.values()]
