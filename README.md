# Claude Chats & Analytics Viewer

> Browse, search, and resume your [Claude Code](https://docs.anthropic.com/en/docs/claude-code) conversations — from the browser or terminal.

[![PyPI version](https://img.shields.io/pypi/v/claude-chats-and-analytics-viewer?color=8b5cf6&label=PyPI)](https://pypi.org/project/claude-chats-and-analytics-viewer/)
[![Python 3.7+](https://img.shields.io/badge/python-3.7%2B-blue)](https://python.org)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-none-22c55e)](https://pypi.org/project/claude-chats-and-analytics-viewer/)
[![Cross Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)](#install--run)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

---

## What is this?

Claude Code stores all your conversations as local files. This tool gives you a **beautiful web UI and terminal CLI** to:

- Browse every conversation you've ever had with Claude Code
- Search across titles or deep-search inside every message
- See cost estimates, token usage, and model breakdowns
- Bookmark important conversations
- Export chats as Markdown or JSON
- Resume any past conversation directly in Claude Code

**100% local — no data leaves your machine.**

---

## Install & Run

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/AnshRajput/claude-chats-and-analytics-viewer/main/install.sh | sh
```

### Windows (PowerShell)

```powershell
iwr https://raw.githubusercontent.com/AnshRajput/claude-chats-and-analytics-viewer/main/install.ps1 | iex
```

The installer handles everything — installs `pipx` if needed, installs the package, and opens the viewer automatically.

### Manual install

```bash
pipx install claude-chats-and-analytics-viewer    # recommended
uvx claude-chats-and-analytics-viewer             # uv, no install needed
pip3 install --user claude-chats-and-analytics-viewer
```

> Don't have `pipx`?
> - macOS: `brew install pipx && pipx ensurepath`
> - Linux: `python3 -m pip install --user pipx && python3 -m pipx ensurepath`
> - Windows: `pip install pipx && pipx ensurepath`
>
> After running `ensurepath`, open a new terminal and run `ccv`.

---

## Features

### Web UI
| Feature | Description |
|---|---|
| **Conversation browser** | Search, filter by project, sort by date / tokens / cost |
| **Cost estimation** | Per-conversation and per-message USD cost based on model pricing |
| **Deep search** | Toggle `DEEP` to search inside every message, not just titles |
| **Bookmarks** | Star any conversation — persisted across sessions |
| **Activity heatmap** | GitHub-style 52-week calendar showing your Claude usage |
| **Copy buttons** | One-click copy on every code block |
| **Auto-refresh** | Detects new conversations while the viewer is open |
| **Export** | Download as `.md`, `.json`, or export everything as a `.zip` |
| **Smart caching** | Near-instant startup after first run |
| **Usage stats** | Token counts, model breakdown, top projects, total cost |

### Terminal CLI
| Feature | Description |
|---|---|
| **Interactive browser** | Paginated list with colored output and keyboard navigation |
| **Search** | Filter conversations by keyword or project |
| **View** | Read full conversation messages in the terminal |
| **Resume** | Jump directly back into any past conversation in Claude Code |
| **Bookmarks** | Toggle bookmarks from the terminal |

---

## Commands

### Web UI (`ccv`)

```bash
ccv                        # start the viewer (opens browser automatically)
ccv --port 8080            # use a different port
ccv --no-open              # start server without opening browser
ccv --update               # update to the latest version
ccv --install              # auto-start on login (macOS LaunchAgent)
ccv --install-systemd      # auto-start on login (Linux systemd)
ccv --uninstall            # remove the auto-start service
```

### Terminal CLI (`claude-conversations-cli`)

```bash
claude-conversations-cli                          # interactive browser
claude-conversations-cli --search "auth bug"      # search by keyword
claude-conversations-cli --project "myapp"        # filter by project
claude-conversations-cli --view <session-id>      # view a conversation
claude-conversations-cli --resume <session-id>    # resume in Claude Code
claude-conversations-cli --list                   # non-interactive list (pipe-friendly)
claude-conversations-cli --list --limit 10        # limit results
```

### Interactive CLI keyboard commands

| Key | Action |
|---|---|
| `3` | Show details for conversation #3 |
| `v 3` | Read full messages of conversation #3 |
| `r 3` | Resume conversation #3 in Claude Code |
| `b 3` | Toggle bookmark on conversation #3 |
| `s <query>` | Search conversations |
| `a` | Clear search, show all |
| `n` / `p` | Next / previous page |
| `h` | Show help |
| `q` | Quit |

---

## Update

```bash
ccv --update
# or
pipx upgrade claude-chats-and-analytics-viewer
```

When an update is available, a banner appears in the Web UI and a notice prints in the CLI automatically.

---

## How it works

Claude Code saves conversations as JSONL files at:

```
~/.claude/projects/<project-name>/<session-id>.jsonl          # macOS / Linux
%USERPROFILE%\.claude\projects\<project-name>\<session-id>.jsonl  # Windows
```

This tool scans those files, caches the metadata for fast startup, and serves a local web UI at `http://127.0.0.1:5005`. Nothing is uploaded or shared anywhere.

---

## Troubleshooting

**`ccv` not found after install**
Run `pipx ensurepath`, then open a new terminal.

**Port already in use**
`ccv` automatically switches to the next available port and tells you which one it picked.
Or specify one manually: `ccv --port 8080`

**No conversations found**
Make sure you've used Claude Code at least once. Check that `~/.claude/projects/` exists and contains `.jsonl` files:
```bash
ls ~/.claude/projects/
```

---

## License

MIT — [github.com/AnshRajput/claude-chats-and-analytics-viewer](https://github.com/AnshRajput/claude-chats-and-analytics-viewer)
