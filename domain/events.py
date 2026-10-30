from dataclasses import dataclass, field
import json
from typing import Any, Iterable, Mapping

from langchain_core.messages import AIMessage, ToolMessage


@dataclass(frozen=True)
class AppEvent:
    type: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


def iter_app_events(events: Iterable[Mapping[str, Any]]) -> Iterable[AppEvent]:
    """Converte valores do LangGraph no DTO único consumido pelas interfaces."""
    for raw_event in events:
        if not isinstance(raw_event, Mapping):
            continue
        state = dict(raw_event)
        if "__interrupt__" in state:
            yield AppEvent("status", "approval_required", {"state": state})
        if state.get("status") == "failed":
            yield AppEvent(
                "error",
                "\n".join(str(item) for item in state.get("errors", [])),
                {"state": state},
            )
        messages = state.get("messages") or []
        if not messages:
            yield AppEvent("status", state.get("status", "updated"), {"state": state})
            continue
        message = messages[-1]
        if isinstance(message, AIMessage) and message.content:
            yield AppEvent("message", str(message.content), {"state": state})
        elif isinstance(message, ToolMessage):
            try:
                payload = json.loads(str(message.content))
            except (TypeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict) and payload.get("tipo") == "ficheiro_gerado":
                yield AppEvent("artifact", payload.get("filename", ""), {**payload, "state": state})
            else:
                yield AppEvent("status", state.get("status", "updated"), {"state": state})

