"""Session management commands: /sessions, /switch, /name"""
from __future__ import annotations

from time import time
from typing import TYPE_CHECKING

from ..chat_sessions import ChatSessionStore, SessionInfo
from ..types import TelegramIncomingMessage
from .reply import make_reply

if TYPE_CHECKING:
    from ..bridge import TelegramBridgeConfig


def _format_time_ago(timestamp: float) -> str:
    """Format timestamp as relative time."""
    if timestamp == 0:
        return "unknown"
    diff = time() - timestamp
    if diff < 60:
        return "just now"
    if diff < 3600:
        mins = int(diff / 60)
        return f"{mins}m ago"
    if diff < 86400:
        hours = int(diff / 3600)
        return f"{hours}h ago"
    days = int(diff / 86400)
    return f"{days}d ago"


def _format_session(
    session: SessionInfo, *, is_active: bool = False, index: int | None = None
) -> str:
    """Format a single session for display."""
    prefix = ""
    if index is not None:
        prefix = f"{index}. "

    active_marker = "▸ " if is_active else "  "
    title = session.title or session.first_message or "untitled"

    # Truncate title if too long
    if len(title) > 30:
        title = title[:27] + "..."

    time_str = _format_time_ago(session.updated_at)
    short_id = session.resume[:8]

    return f"{prefix}{active_marker}`{short_id}` {title} ({time_str})"


def _short_id(resume_id: str) -> str:
    """Get short version of resume ID for display."""
    return resume_id[:8]


async def handle_sessions_command(
    cfg: "TelegramBridgeConfig",
    msg: TelegramIncomingMessage,
    args_text: str,
    store: ChatSessionStore,
    session_key: tuple[int, int | None] | None,
    default_engine: str,
) -> None:
    """Handle /sessions command - list all sessions."""
    reply = make_reply(cfg, msg)

    if session_key is None:
        await reply(text="session tracking not available.")
        return

    chat_id, owner_id = session_key

    # Parse optional engine filter
    engine_filter = args_text.strip() if args_text.strip() else None

    sessions = await store.list_sessions(chat_id, owner_id, engine=engine_filter)

    if not sessions:
        await reply(text="no sessions found. start chatting to create one!")
        return

    # Get active session IDs
    active_ids: dict[str, str | None] = {}
    for engine in set(s.engine for s in sessions):
        active_ids[engine] = await store.get_active_session_id(chat_id, owner_id, engine)

    # Group by engine
    by_engine: dict[str, list[SessionInfo]] = {}
    for s in sessions:
        by_engine.setdefault(s.engine, []).append(s)

    lines = ["**your sessions:**\n"]

    for engine, engine_sessions in by_engine.items():
        active_id = active_ids.get(engine)
        lines.append(f"**{engine}:**")

        for i, session in enumerate(engine_sessions[:10], 1):  # Show max 10 per engine
            is_active = session.resume == active_id
            lines.append(_format_session(session, is_active=is_active, index=i))

        if len(engine_sessions) > 10:
            lines.append(f"  ... and {len(engine_sessions) - 10} more")
        lines.append("")

    lines.append("commands:")
    lines.append("`/switch <id>` - switch to session")
    lines.append("`/name <title>` - name current session")
    lines.append("`/new` - start fresh (keeps history)")

    # Build inline keyboard with session buttons
    keyboard = []
    for session in sessions[:6]:  # Max 6 buttons
        is_active = session.resume == active_ids.get(session.engine)
        if not is_active:
            title = session.title or session.first_message or _short_id(session.resume)
            if len(title) > 20:
                title = title[:17] + "..."
            keyboard.append([{
                "text": f"↩️ {title}",
                "callback_data": f"takopi:switch:{session.resume[:32]}"
            }])

    await reply(
        text="\n".join(lines),
        reply_markup={"inline_keyboard": keyboard} if keyboard else None,
    )


async def handle_switch_command(
    cfg: "TelegramBridgeConfig",
    msg: TelegramIncomingMessage,
    args_text: str,
    store: ChatSessionStore,
    session_key: tuple[int, int | None] | None,
) -> None:
    """Handle /switch <session_id> command."""
    reply = make_reply(cfg, msg)

    if session_key is None:
        await reply(text="session tracking not available.")
        return

    chat_id, owner_id = session_key
    resume_id = args_text.strip()

    if not resume_id:
        await reply(text="usage: `/switch <session_id>`\nuse `/sessions` to see available sessions.")
        return

    # Try to find session by partial ID match
    sessions = await store.list_sessions(chat_id, owner_id)
    matching = [s for s in sessions if s.resume.startswith(resume_id)]

    if not matching:
        await reply(text=f"no session found matching `{resume_id}`")
        return

    if len(matching) > 1:
        await reply(text=f"multiple sessions match `{resume_id}`. be more specific.")
        return

    session = await store.switch_session(chat_id, owner_id, matching[0].resume)
    if session is None:
        await reply(text="failed to switch session.")
        return

    title = session.title or session.first_message or _short_id(session.resume)
    await reply(text=f"switched to: `{title}`\n\nresume: `claude --resume {session.resume}`")


async def handle_name_command(
    cfg: "TelegramBridgeConfig",
    msg: TelegramIncomingMessage,
    args_text: str,
    store: ChatSessionStore,
    session_key: tuple[int, int | None] | None,
    default_engine: str,
) -> None:
    """Handle /name <title> command."""
    reply = make_reply(cfg, msg)

    if session_key is None:
        await reply(text="session tracking not available.")
        return

    chat_id, owner_id = session_key
    title = args_text.strip()

    if not title:
        await reply(text="usage: `/name <title>`\nexample: `/name API refactoring`")
        return

    success = await store.name_session(chat_id, owner_id, default_engine, title)
    if not success:
        await reply(text="no active session to name. start a conversation first.")
        return

    await reply(text=f"session named: `{title}`")


async def handle_delete_command(
    cfg: "TelegramBridgeConfig",
    msg: TelegramIncomingMessage,
    args_text: str,
    store: ChatSessionStore,
    session_key: tuple[int, int | None] | None,
) -> None:
    """Handle /delete <session_id> command."""
    reply = make_reply(cfg, msg)

    if session_key is None:
        await reply(text="session tracking not available.")
        return

    chat_id, owner_id = session_key
    resume_id = args_text.strip()

    if not resume_id:
        await reply(text="usage: `/delete <session_id>`\nuse `/sessions` to see available sessions.")
        return

    # Try to find session by partial ID match
    sessions = await store.list_sessions(chat_id, owner_id)
    matching = [s for s in sessions if s.resume.startswith(resume_id)]

    if not matching:
        await reply(text=f"no session found matching `{resume_id}`")
        return

    if len(matching) > 1:
        await reply(text=f"multiple sessions match `{resume_id}`. be more specific.")
        return

    session = matching[0]
    title = session.title or session.first_message or _short_id(session.resume)

    success = await store.delete_session(chat_id, owner_id, session.resume)
    if not success:
        await reply(text="failed to delete session.")
        return

    await reply(text=f"deleted session: `{title}`")


async def handle_switch_callback(
    cfg: "TelegramBridgeConfig",
    store: ChatSessionStore,
    session_key: tuple[int, int | None] | None,
    resume_id_prefix: str,
) -> str:
    """Handle switch button callback. Returns response text."""
    if session_key is None:
        return "session tracking not available."

    chat_id, owner_id = session_key

    # Find session by prefix
    sessions = await store.list_sessions(chat_id, owner_id)
    matching = [s for s in sessions if s.resume.startswith(resume_id_prefix)]

    if not matching:
        return f"session not found"

    session = await store.switch_session(chat_id, owner_id, matching[0].resume)
    if session is None:
        return "failed to switch session"

    title = session.title or session.first_message or _short_id(session.resume)
    return f"switched to: {title}"
