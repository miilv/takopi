"""File-based message injection for cron triggers and system messages."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import anyio

from ..context import RunContext
from ..logging import get_logger
from ..model import ResumeToken

logger = get_logger(__name__)

SYSTEM_PREFIX = "[SYSTEM] "


async def watch_inject_dir(
    *,
    inject_dir: Path,
    chat_id: int,
    poll_interval: float = 2.0,
    run_job: Callable[
        [int, int, str, ResumeToken | None, RunContext | None],
        Awaitable[None],
    ],
    get_resume: Callable[[int], Awaitable[ResumeToken | None]],
    clear_session: Callable[[int], Awaitable[None]],
) -> None:
    """Poll inject_dir for JSON files and dispatch them as system messages.

    Each JSON file should contain:
        {"text": "morning check", "new_session": false}

    The text is wrapped with [SYSTEM] prefix and routed through the normal
    execution pipeline. If new_session is true, the active session is cleared
    first so Claude starts a fresh conversation.
    """
    inject_dir.mkdir(parents=True, exist_ok=True)
    logger.info("inject.watcher.started", inject_dir=str(inject_dir))

    while True:
        try:
            files = sorted(inject_dir.glob("*.json"))
            for fpath in files:
                try:
                    raw = fpath.read_text(encoding="utf-8")
                    payload = json.loads(raw)
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning(
                        "inject.file.invalid",
                        path=str(fpath),
                        error=str(exc),
                    )
                    try:
                        fpath.rename(fpath.with_suffix(".bad"))
                    except OSError:
                        pass
                    continue

                try:
                    fpath.unlink()
                except OSError:
                    pass

                text = payload.get("text", "").strip()
                if not text:
                    logger.warning("inject.file.empty_text", path=fpath.name)
                    continue

                new_session = payload.get("new_session", False)

                logger.info(
                    "inject.dispatch",
                    text=text,
                    new_session=new_session,
                    file=fpath.name,
                )

                if new_session:
                    await clear_session(chat_id)

                resume_token = await get_resume(chat_id)
                prompt = f"{SYSTEM_PREFIX}{text}"

                await run_job(chat_id, 0, prompt, resume_token, None)

        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "inject.watcher.error",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )

        await anyio.sleep(poll_interval)
