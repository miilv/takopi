# Takopi

Telegram bridge for coding agents (Claude Code, Codex, OpenCode, Pi). Lets you send tasks to agents from Telegram, stream progress live, and resume sessions anywhere.

**Author:** banteg
**Python:** >=3.14, uses `uv` for packaging
**Entry point:** `takopi.cli:main` → `takopi` command

## Architecture

```
User (Telegram) → Transport → Router → Runner → Agent CLI subprocess
                                                    ↓
                              Transport ← Presenter ← JSONL Events
```

### Core layers

- **Model** (`model.py`, `events.py`): Domain types — `ResumeToken`, `Action`, `StartedEvent`/`ActionEvent`/`CompletedEvent`
- **Runner** (`runner.py`, `runners/`): Execute agent CLIs as subprocesses, parse JSONL stdout. `JsonlSubprocessRunner` is the base. Runners: `claude.py`, `codex.py`, `opencode.py`, `pi.py`, `mock.py`
- **Router** (`router.py`): `AutoRouter` selects runner by engine ID, tracks availability
- **Runner Bridge** (`runner_bridge.py`): Connects runners to transport, manages progress edits, cancellation. `handle_message()` orchestrates the full flow
- **Transport** (`transport.py`): Protocol for send/edit/delete messages. Currently only Telegram
- **Config** (`config.py`, `settings.py`): TOML-based config at `~/.takopi/takopi.toml`. `TakopiSettings` (Pydantic) validates everything
- **Plugins** (`plugins.py`, `engines.py`): Entry-point discovery for engines, transports, commands

### Telegram transport (`telegram/`)

- `bridge.py`: `TelegramBridgeConfig`, `TelegramPresenter`, `TelegramTransport`, main loop glue
- `client.py`: HTTP wrapper for Telegram Bot API
- `loop.py`: Polling loop, message routing, command dispatch — this is the main event loop
- `commands/`: Subpackage with command handlers — `agent.py`, `sessions.py`, `menu.py`, `topics.py`, `file_transfer.py`, `voice.py`, `executor.py`, `dispatch.py`
- `chat_sessions.py`: Session state persistence (JSON file), multi-session history
- `state_store.py`: Generic JSON state store base
- `voice.py`: Voice transcription (OpenAI + SpeechCore providers)
- `parsing.py`, `render.py`: Message parsing and markdown rendering

### Other

- `directives.py`: Parses `/codex @branch prompt` syntax into engine, branch, prompt
- `worktrees.py`: Git worktree management per branch
- `progress.py`, `markdown.py`: Progress tracking and message formatting
- `cli/`: CLI commands — `run.py` (main loop), `config.py`, `init.py`, `doctor.py`, `onboarding_cmd.py`
- `utils/`: Git helpers, path ops, async streams, subprocess management, JSON state

## Uncommitted local changes (9 files, +488/-59 lines)

### 1. Multi-session support (`chat_sessions.py`)
- Migrated from single session per engine to multi-session history
- `SessionInfo` struct with resume, engine, title, first_message, timestamps
- `_ChatState` now has `history` (all sessions) + `active` (per engine) + migration from old format
- New methods: `list_sessions`, `switch_session`, `name_session`, `delete_session`, `new_session`, `get_active_session_id`
- Auto-prunes to `MAX_SESSIONS_PER_CHAT = 20` per engine
- State version bumped to 2

### 2. Session commands (`commands/sessions.py`) — NEW FILE
- `/sessions [engine]` — list sessions grouped by engine with inline keyboard buttons
- `/switch <id>` — switch to session by partial ID match
- `/name <title>` — name the active session
- `/delete <id>` — delete a session by partial ID
- `handle_switch_callback` — handles inline button callback for switching

### 3. SpeechCore voice transcription (`voice.py`, `settings.py`, `backend.py`, `bridge.py`)
- New `SpeechCoreTranscriber` class using speechcoreai.com API
- Upload → poll for completion → fetch result flow
- Settings: `voice_transcription_provider` (openai|speechcore), `voice_speechcore_api_key`, `voice_speechcore_language`, `voice_speechcore_diarize`
- Provider selection in `loop.py` based on config

### 4. File transfer improvements (`commands/file_transfer.py`)
- `/get` now supports absolute paths directly (bypass project context)
- Falls back to `Path.home()` if no project context available

### 5. Other changes
- `bridge.py`: `send_plain()` now accepts `reply_markup` for inline keyboards
- `loop.py`: Wired up session commands, callback query handling for `takopi:switch:` prefix, passes `first_message` to session store, added `chat_session_store`/`chat_session_key`/`default_engine` to `TelegramCommandContext`
- `commands/menu.py`: Added sessions/switch/name/delete to bot commands
- `commands/topics.py`: `/new` now says "new session started" instead of "cleared sessions"

## Development

```bash
just test          # run tests
just lint          # ruff check
just fmt           # ruff format
just typecheck     # ty check
just docs          # build docs
```

## Deployment

Running as a systemd user service on the host machine (`~`):

```bash
systemctl --user status takopi    # check status
systemctl --user restart takopi   # restart
journalctl --user -u takopi -f    # follow live logs
```

- Service file: `~/.config/systemd/user/takopi.service`
- WorkingDirectory: `/home/agent`
- ExecStart: `/home/agent/takopi/.venv/bin/takopi`
- Auto-restarts on crash (5s delay)
- User lingering enabled — survives logouts and reboots
- The CLI entrypoint is just `takopi` (no subcommand), not `takopi run`

## Config location

`~/.takopi/takopi.toml` — see `settings.py` for all options. Key sections:
- `default_engine`: which agent to use by default
- `[transports.telegram]`: bot token, chat_id, session_mode, voice settings
- `[transports.telegram.files]`: file upload/download settings
- `[projects.<name>]`: project paths, worktree dirs, per-project engine
