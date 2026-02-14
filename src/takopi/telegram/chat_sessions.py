from __future__ import annotations

from pathlib import Path
from time import time

import msgspec

from ..logging import get_logger
from ..model import ResumeToken
from .state_store import JsonStateStore

logger = get_logger(__name__)

STATE_VERSION = 2  # Bumped for multi-session support
STATE_FILENAME = "telegram_chat_sessions_state.json"
MAX_SESSIONS_PER_CHAT = 20  # Keep last N sessions per engine


class SessionInfo(msgspec.Struct, forbid_unknown_fields=False):
    """Information about a single session."""
    resume: str
    engine: str
    title: str | None = None
    first_message: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0


class _ChatState(msgspec.Struct, forbid_unknown_fields=False):
    # All sessions keyed by resume ID
    history: dict[str, SessionInfo] = msgspec.field(default_factory=dict)
    # Active session resume ID per engine
    active: dict[str, str] = msgspec.field(default_factory=dict)
    # Legacy field for migration
    sessions: dict[str, dict] | None = None


class _ChatSessionsState(msgspec.Struct, forbid_unknown_fields=False):
    version: int
    cwd: str | None = None
    chats: dict[str, _ChatState] = msgspec.field(default_factory=dict)


def resolve_sessions_path(config_path: Path) -> Path:
    return config_path.with_name(STATE_FILENAME)


def _chat_key(chat_id: int, owner_id: int | None) -> str:
    owner = "chat" if owner_id is None else str(owner_id)
    return f"{chat_id}:{owner}"


def _new_state() -> _ChatSessionsState:
    return _ChatSessionsState(version=STATE_VERSION, chats={})


def _migrate_chat_state(chat: _ChatState) -> None:
    """Migrate from old single-session format to multi-session."""
    if chat.sessions is not None:
        now = time()
        for engine, old_data in chat.sessions.items():
            if isinstance(old_data, dict) and "resume" in old_data:
                resume = old_data["resume"]
                if resume and resume not in chat.history:
                    chat.history[resume] = SessionInfo(
                        resume=resume,
                        engine=engine,
                        created_at=now,
                        updated_at=now,
                    )
                    chat.active[engine] = resume
        chat.sessions = None


class ChatSessionStore(JsonStateStore[_ChatSessionsState]):
    def __init__(self, path: Path) -> None:
        super().__init__(
            path,
            version=STATE_VERSION,
            state_type=_ChatSessionsState,
            state_factory=_new_state,
            log_prefix="telegram.chat_sessions",
            logger=logger,
        )

    def _migrate_if_needed(self) -> None:
        """Migrate all chats from old format."""
        for chat in self._state.chats.values():
            _migrate_chat_state(chat)

    async def get_session_resume(
        self, chat_id: int, owner_id: int | None, engine: str
    ) -> ResumeToken | None:
        """Get the active session for an engine."""
        async with self._lock:
            self._reload_locked_if_needed()
            self._migrate_if_needed()
            chat = self._get_chat_locked(chat_id, owner_id)
            if chat is None:
                return None
            resume_id = chat.active.get(engine)
            if not resume_id:
                return None
            session = chat.history.get(resume_id)
            if session is None:
                return None
            return ResumeToken(engine=engine, value=session.resume)

    async def sync_startup_cwd(self, cwd: Path) -> bool:
        normalized = str(cwd.expanduser().resolve())
        async with self._lock:
            self._reload_locked_if_needed()
            previous = self._state.cwd
            cleared = False
            if previous is not None and previous != normalized:
                self._state.chats = {}
                cleared = True
            if previous != normalized:
                self._state.cwd = normalized
                self._save_locked()
            return cleared

    async def set_session_resume(
        self,
        chat_id: int,
        owner_id: int | None,
        token: ResumeToken,
        *,
        first_message: str | None = None,
    ) -> None:
        """Set or update a session. Creates new if doesn't exist."""
        async with self._lock:
            self._reload_locked_if_needed()
            self._migrate_if_needed()
            if self._state.cwd is None:
                self._state.cwd = str(Path.cwd().expanduser().resolve())
            chat = self._ensure_chat_locked(chat_id, owner_id)
            now = time()

            existing = chat.history.get(token.value)
            if existing:
                # Update existing session
                existing.updated_at = now
                if first_message and not existing.first_message:
                    existing.first_message = first_message[:100]
            else:
                # Create new session
                chat.history[token.value] = SessionInfo(
                    resume=token.value,
                    engine=token.engine,
                    first_message=first_message[:100] if first_message else None,
                    created_at=now,
                    updated_at=now,
                )

            # Set as active
            chat.active[token.engine] = token.value

            # Prune old sessions if too many
            self._prune_sessions_locked(chat, token.engine)
            self._save_locked()

    async def clear_sessions(self, chat_id: int, owner_id: int | None) -> None:
        """Clear only the active session pointers, keep history."""
        async with self._lock:
            self._reload_locked_if_needed()
            self._migrate_if_needed()
            chat = self._get_chat_locked(chat_id, owner_id)
            if chat is None:
                return
            chat.active = {}
            self._save_locked()

    async def new_session(
        self, chat_id: int, owner_id: int | None, engine: str
    ) -> None:
        """Start a new session for an engine (keeps old in history)."""
        async with self._lock:
            self._reload_locked_if_needed()
            self._migrate_if_needed()
            chat = self._get_chat_locked(chat_id, owner_id)
            if chat is None:
                return
            # Just clear the active pointer, history remains
            if engine in chat.active:
                del chat.active[engine]
            self._save_locked()

    async def list_sessions(
        self, chat_id: int, owner_id: int | None, engine: str | None = None
    ) -> list[SessionInfo]:
        """List all sessions, optionally filtered by engine."""
        async with self._lock:
            self._reload_locked_if_needed()
            self._migrate_if_needed()
            chat = self._get_chat_locked(chat_id, owner_id)
            if chat is None:
                return []

            sessions = list(chat.history.values())
            if engine:
                sessions = [s for s in sessions if s.engine == engine]

            # Sort by updated_at descending
            sessions.sort(key=lambda s: s.updated_at, reverse=True)
            return sessions

    async def get_active_session_id(
        self, chat_id: int, owner_id: int | None, engine: str
    ) -> str | None:
        """Get the active session ID for an engine."""
        async with self._lock:
            self._reload_locked_if_needed()
            self._migrate_if_needed()
            chat = self._get_chat_locked(chat_id, owner_id)
            if chat is None:
                return None
            return chat.active.get(engine)

    async def switch_session(
        self, chat_id: int, owner_id: int | None, resume_id: str
    ) -> SessionInfo | None:
        """Switch to a different session by resume ID."""
        async with self._lock:
            self._reload_locked_if_needed()
            self._migrate_if_needed()
            chat = self._get_chat_locked(chat_id, owner_id)
            if chat is None:
                return None

            session = chat.history.get(resume_id)
            if session is None:
                return None

            # Set as active for its engine
            chat.active[session.engine] = resume_id
            session.updated_at = time()
            self._save_locked()
            return session

    async def name_session(
        self, chat_id: int, owner_id: int | None, engine: str, title: str
    ) -> bool:
        """Set a title for the active session."""
        async with self._lock:
            self._reload_locked_if_needed()
            self._migrate_if_needed()
            chat = self._get_chat_locked(chat_id, owner_id)
            if chat is None:
                return False

            resume_id = chat.active.get(engine)
            if not resume_id:
                return False

            session = chat.history.get(resume_id)
            if session is None:
                return False

            session.title = title[:50]
            self._save_locked()
            return True

    async def delete_session(
        self, chat_id: int, owner_id: int | None, resume_id: str
    ) -> bool:
        """Delete a session from history."""
        async with self._lock:
            self._reload_locked_if_needed()
            self._migrate_if_needed()
            chat = self._get_chat_locked(chat_id, owner_id)
            if chat is None:
                return False

            if resume_id not in chat.history:
                return False

            session = chat.history[resume_id]
            del chat.history[resume_id]

            # Clear active pointer if this was active
            if chat.active.get(session.engine) == resume_id:
                del chat.active[session.engine]

            self._save_locked()
            return True

    def _prune_sessions_locked(self, chat: _ChatState, engine: str) -> None:
        """Remove oldest sessions if over limit."""
        engine_sessions = [
            (rid, s) for rid, s in chat.history.items() if s.engine == engine
        ]
        if len(engine_sessions) <= MAX_SESSIONS_PER_CHAT:
            return

        # Sort by updated_at ascending (oldest first)
        engine_sessions.sort(key=lambda x: x[1].updated_at)

        # Remove oldest, keeping MAX_SESSIONS_PER_CHAT
        to_remove = len(engine_sessions) - MAX_SESSIONS_PER_CHAT
        for rid, _ in engine_sessions[:to_remove]:
            # Don't remove active session
            if chat.active.get(engine) != rid:
                del chat.history[rid]

    def _get_chat_locked(self, chat_id: int, owner_id: int | None) -> _ChatState | None:
        return self._state.chats.get(_chat_key(chat_id, owner_id))

    def _ensure_chat_locked(self, chat_id: int, owner_id: int | None) -> _ChatState:
        key = _chat_key(chat_id, owner_id)
        entry = self._state.chats.get(key)
        if entry is not None:
            return entry
        entry = _ChatState()
        self._state.chats[key] = entry
        return entry
