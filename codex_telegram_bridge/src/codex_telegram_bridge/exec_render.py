from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from textwrap import indent
from typing import Any, Dict, List, Optional


def _truncate_output(text: str, max_lines: int = 20, max_chars: int = 4000) -> str:
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[-max_chars:]
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = ["..."] + lines[-max_lines:]
    return "\n".join(lines)


def _format_todo(items: list[dict[str, Any]]) -> str:
    rendered: list[str] = []
    for item in items:
        status = "done" if item.get("completed") else "todo"
        rendered.append(f"- [{status}] {item.get('text', '')}")
    return "\n".join(rendered)


def _format_todo_summary(items: list[dict[str, Any]]) -> str:
    total = len(items)
    done = sum(1 for item in items if item.get("completed"))
    next_text = next((item.get("text", "") for item in items if not item.get("completed")), "")
    if next_text:
        return f"plan: {done}/{total} done, next: {_shorten(next_text, 80)}"
    return f"plan: {done}/{total} done"


def _maybe_parse_json(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _shorten(text: str, max_len: int = 140) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


@dataclass
class ExecRenderState:
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    recent: deque[str] = field(default_factory=lambda: deque(maxlen=4))
    last_turn: Optional[int] = None


def _record_item(state: ExecRenderState, item: dict[str, Any]) -> None:
    item_id = item.get("id")
    if isinstance(item_id, str) and item_id:
        state.items[item_id] = item
        match = re.search(r"item_(\d+)", item_id)
        if match:
            state.last_turn = int(match.group(1))


def render_event_cli(
    event: dict[str, Any],
    state: ExecRenderState,
    *,
    show_reasoning: bool = False,
) -> list[str]:
    etype = event.get("type")
    lines: list[str] = []

    if etype == "thread.started":
        thread_id = event.get("thread_id", "")
        if thread_id:
            lines.append(f"thread started: {thread_id}")
            lines.append(f"resume with: codex exec resume {thread_id}")
        else:
            lines.append("thread started")
        return lines

    if etype == "turn.started":
        return ["turn started"]

    if etype == "turn.completed":
        usage = event.get("usage", {})
        lines.append(
            "turn completed "
            f"(in={usage.get('input_tokens', 0)} "
            f"cached={usage.get('cached_input_tokens', 0)} "
            f"out={usage.get('output_tokens', 0)})"
        )
        return lines

    if etype == "turn.failed":
        error = event.get("error", {}).get("message", "")
        return [f"turn failed: {error}"]

    if etype == "error":
        return [f"stream error: {event.get('message', '')}"]

    if etype in {"item.started", "item.updated", "item.completed"}:
        item = event.get("item", {}) or {}
        _record_item(state, item)

        itype = item.get("type")
        status = item.get("status")

        if itype == "agent_message":
            text = item.get("text", "")
            parsed = _maybe_parse_json(text)
            if parsed is not None:
                lines.append("assistant (json):")
                lines.extend(indent(json.dumps(parsed, indent=2), "  ").splitlines())
            else:
                lines.append("assistant:")
                lines.extend(indent(text, "  ").splitlines() if text else ["  (empty)"])

        elif itype == "reasoning" and show_reasoning:
            lines.append(f"reasoning: {item.get('text', '')}")

        elif itype == "command_execution":
            command = item.get("command", "")
            if etype == "item.started":
                lines.append(f"run: {command}")
            else:
                exit_code = item.get("exit_code")
                outcome = "ok" if status == "completed" else status or "unknown"
                lines.append(f"command {outcome} (exit={exit_code}): {command}")
                output = _truncate_output(item.get("aggregated_output", ""))
                if output:
                    lines.extend(indent(output, "  ").splitlines())

        elif itype == "file_change":
            changes = item.get("changes", [])
            counts = {"add": 0, "update": 0, "delete": 0}
            for change in changes:
                kind = change.get("kind")
                if kind in counts:
                    counts[kind] += 1
            lines.append(
                "file changes "
                f"(status={status}) add={counts['add']} "
                f"update={counts['update']} delete={counts['delete']}"
            )
            for change in changes:
                lines.append(f"  {change.get('kind')} {change.get('path')}")

        elif itype == "mcp_tool_call":
            server = item.get("server", "")
            tool = item.get("tool", "")
            if etype == "item.started":
                lines.append(f"tool call: {server}.{tool}")
            else:
                outcome = "ok" if status == "completed" else status or "unknown"
                lines.append(f"tool {outcome}: {server}.{tool}")
                if item.get("error"):
                    lines.append(f"  error: {item['error'].get('message', '')}")
                result = item.get("result") or {}
                if result.get("structured_content") is not None:
                    lines.append("  result:")
                    lines.extend(
                        indent(json.dumps(result["structured_content"], indent=2), "    ").splitlines()
                    )

        elif itype == "web_search":
            lines.append(f"web search: {item.get('query', '')}")

        elif itype == "todo_list":
            todo = _format_todo(item.get("items", []))
            lines.append(f"plan ({etype}):")
            lines.extend(indent(todo, "  ").splitlines())

        elif itype == "error":
            lines.append(f"warning: {item.get('message', '')}")

        else:
            lines.append(f"{etype}: {item}")

    else:
        if etype:
            lines.append(f"event: {etype}")
        else:
            lines.append(f"event: {event}")

    return lines


def render_event_progress(event: dict[str, Any], state: ExecRenderState) -> Optional[str]:
    etype = event.get("type")

    if etype == "thread.started":
        thread_id = event.get("thread_id", "")
        return f"thread started: {thread_id}" if thread_id else "thread started"

    if etype == "turn.started":
        return "turn started"

    if etype == "turn.completed":
        usage = event.get("usage", {})
        return (
            "turn completed "
            f"(in={usage.get('input_tokens', 0)} "
            f"cached={usage.get('cached_input_tokens', 0)} "
            f"out={usage.get('output_tokens', 0)})"
        )

    if etype == "turn.failed":
        error = event.get("error", {}).get("message", "")
        return f"turn failed: {error}"

    if etype == "error":
        return f"stream error: {event.get('message', '')}"

    if etype in {"item.started", "item.updated", "item.completed"}:
        item = event.get("item", {}) or {}
        _record_item(state, item)

        itype = item.get("type")
        status = item.get("status")

        if itype == "agent_message" and etype == "item.completed":
            text = item.get("text", "")
            snippet = text.splitlines()[0] if text else ""
            if snippet:
                return f"assistant: {_shorten(snippet, 120)}"
            return "assistant response ready"

        if itype == "command_execution":
            command = item.get("command", "")
            if etype == "item.started":
                return f"run: {_shorten(command, 160)}"
            exit_code = item.get("exit_code")
            outcome = "ok" if status == "completed" else status or "unknown"
            return f"command {outcome} (exit={exit_code}): {_shorten(command, 120)}"

        if itype == "file_change":
            changes = item.get("changes", [])
            counts = {"add": 0, "update": 0, "delete": 0}
            for change in changes:
                kind = change.get("kind")
                if kind in counts:
                    counts[kind] += 1
            return (
                "file changes "
                f"+{counts['add']} ~{counts['update']} -{counts['delete']}"
            )

        if itype == "mcp_tool_call":
            server = item.get("server", "")
            tool = item.get("tool", "")
            if etype == "item.started":
                return f"tool call: {server}.{tool}"
            outcome = "ok" if status == "completed" else status or "unknown"
            return f"tool {outcome}: {server}.{tool}"

        if itype == "web_search":
            return f"web search: {_shorten(item.get('query', ''), 120)}"

        if itype == "todo_list":
            return _format_todo_summary(item.get("items", []))

        if itype == "error":
            return f"warning: {_shorten(item.get('message', ''), 120)}"

    return None


class ExecProgressRenderer:
    def __init__(self, max_lines: int = 4) -> None:
        self.state = ExecRenderState(recent=deque(maxlen=max_lines))

    def note_event(self, event: dict[str, Any]) -> Optional[str]:
        line = render_event_progress(event, self.state)
        if not line:
            return None
        line = line.strip()
        if not line:
            return None
        if self.state.recent and self.state.recent[-1] == line:
            return line
        self.state.recent.append(line)
        return line

    def render(self, header: str) -> str:
        lines: list[str] = []
        if self.state.last_turn is not None:
            lines.append(f"Turn: {self.state.last_turn}")
        if self.state.recent:
            lines.extend(self.state.recent)
        if not lines:
            return header
        return header + "\n\n" + "\n".join(lines)
