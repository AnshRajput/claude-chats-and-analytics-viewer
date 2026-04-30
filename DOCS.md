# Claude Chats & Analytics Viewer — Documentation

Browse, search, export, and resume your [Claude Code](https://docs.anthropic.com/en/docs/claude-code) conversations from the browser or terminal.

---

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
  - [Install from PyPI](#install-from-pypi)
  - [Install from Source](#install-from-source)
  - [Verify Installation](#verify-installation)
- [Web UI](#web-ui)
  - [Starting the Web Server](#starting-the-web-server)
  - [Command-line Options](#web-ui-command-line-options)
  - [Navigating the Interface](#navigating-the-interface)
  - [Searching and Filtering](#searching-and-filtering)
  - [Viewing a Conversation](#viewing-a-conversation)
  - [Exporting Conversations](#exporting-conversations)
  - [Usage Statistics Dashboard](#usage-statistics-dashboard)
  - [Background Service (macOS)](#background-service-macos)
- [CLI](#cli)
  - [Interactive Mode](#interactive-mode)
  - [Command-line Options](#cli-command-line-options)
  - [Interactive Commands](#interactive-commands)
  - [Non-interactive Usage](#non-interactive-usage)
  - [Resuming Conversations](#resuming-conversations)
- [Update Notifications](#update-notifications)
- [How It Works](#how-it-works)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Overview

Claude Chats & Analytics Viewer lets you browse, search, export, and resume your Claude Code conversation history. It provides two interfaces:

- **Web UI** — a browser-based GUI with search, filters, markdown rendering, syntax highlighting, bookmarks, cost estimation, activity heatmap, export, and a usage stats dashboard.
- **CLI** — a terminal-based interactive browser with colored output, box-drawing, search, pagination, and direct resume into Claude Code.

Both interfaces are zero-dependency (Python standard library only), cross-platform (macOS, Windows, Linux), and keep all data local.

---

## Prerequisites

| Requirement | Details |
|---|---|
| **Python** | 3.7 or later |
| **Claude Code** | Installed and used at least once (so `~/.claude/projects/` exists with conversation files) |
| **pip3** | For PyPI installation (use `pip3` on macOS/Linux, `pip` on Windows) |
| **Claude Code CLI** | Required only for the `--resume` / `r` command to jump back into a conversation |

### Where Claude Code stores conversations

Claude Code saves each conversation as a JSONL file at:

```
~/.claude/projects/<project-slug>/<session-id>.jsonl
```

On Windows, the equivalent path is:

```
%USERPROFILE%\.claude\projects\<project-slug>\<session-id>.jsonl
```

The viewer auto-detects the correct path on each platform.

---

## Installation

### Install from PyPI

```bash
pip3 install claude-chats-and-analytics-viewer && ccv
```

This installs the following commands:

| Command | Description |
|---|---|
| `ccv` | Start Web UI (shortest alias) |
| `claude-conversations` | Start Web UI |
| `claude-dashboard` | Start Web UI |
| `claude-conversations-cli` | Start the terminal CLI |

> **Note:** If pip3 installs scripts to a directory not on your PATH (e.g., `~/.local/bin` on Linux or `~/Library/Python/3.x/bin` on macOS), add it:
> ```bash
> export PATH="$HOME/.local/bin:$PATH"
> ```

### Install from Source

```bash
git clone https://github.com/AnshRajput/claude-chats-and-analytics-viewer.git
cd claude-chats-and-analytics-viewer
pip3 install .
```

### Verify Installation

```bash
ccv --help
claude-conversations-cli --help
```

Check the installed version:

```bash
python3 -c "from claude_conversation_viewer import __version__; print(__version__)"
```

---

## Web UI

### Starting the Web Server

```bash
ccv
```

Opens `http://127.0.0.1:5005` in your default browser. On startup the viewer scans conversation files and loads metadata — this is fast after the first run thanks to smart caching.

### Web UI Command-line Options

| Flag | Default | Description |
|---|---|---|
| `--port PORT` | `5005` | Port to serve on |
| `--no-open` | Off | Don't auto-open the browser |
| `--update` | — | Update to latest version |
| `--install` | — | Install as a macOS LaunchAgent (auto-start on login) |
| `--install-systemd` | — | Install as a Linux systemd user service |
| `--uninstall` | — | Remove the macOS LaunchAgent |

Examples:

```bash
ccv --port 8080           # custom port
ccv --no-open             # headless / server mode
ccv --update              # update to latest version
```

### Navigating the Interface

The Web UI has two panels:

- **Sidebar (left)** — conversation list with search, project filter, sort controls, and a Stats tab.
- **Main panel (right)** — conversation viewer with full message history.

Three tabs at the top of the sidebar:

- **Conversations** — the browsable list
- **Bookmarks** — starred/pinned conversations
- **Stats** — usage statistics and activity heatmap

### Searching and Filtering

| Control | Description |
|---|---|
| **Search box** | Filters by title, project path, or model. Toggle `DEEP` to search inside every message. |
| **Project filter** | Show only conversations from a specific project. |
| **Sort order** | Newest, Oldest, Most messages, Most tokens, Highest cost |

### Viewing a Conversation

Click any conversation to view it. The main panel shows:

- **Header** — title, estimated cost badge, export buttons
- **Session bar** — full session ID, copy resume command button
- **Messages** — full chat with user/assistant messages, collapsible tool use blocks, syntax-highlighted code blocks with copy buttons, token usage badges

### Exporting Conversations

| Format | Description |
|---|---|
| **Export .md** | Markdown file with metadata header and all messages |
| **Export .json** | JSON file with full metadata and structured content |
| **Export All** | Downloads a `.zip` of all conversations |

### Usage Statistics Dashboard

Click the **Stats** tab to see:

- Summary cards — conversations, projects, messages, tokens, total estimated cost
- Model usage breakdown
- Top projects by conversation count
- Activity heatmap — GitHub-style 52-week calendar

### Background Service (macOS)

```bash
ccv --install               # install with default port 5005
ccv --install --port 8080   # install with custom port
```

Creates a LaunchAgent at `~/Library/LaunchAgents/com.claude-conversation-viewer.plist` that auto-starts on login.

```bash
ccv --uninstall             # remove
launchctl list | grep claude-conversation   # check status
```

**Linux:** Use the systemd service flag:

```bash
ccv --install-systemd
```

---

## CLI

### Interactive Mode

```bash
claude-conversations-cli
```

Launches an interactive terminal browser with a styled banner, paginated list, search, and conversation viewer.

### CLI Command-line Options

| Flag | Description |
|---|---|
| `--list` | Print conversations non-interactively (pipe-friendly) |
| `--search QUERY` | Filter by keyword |
| `--project NAME` | Filter by project name |
| `--view SESSION_ID` | View a specific conversation |
| `--resume SESSION_ID` | Resume a conversation in Claude Code |
| `--limit N` | Max conversations in `--list` mode (default: 50) |

### Interactive Commands

| Command | Action |
|---|---|
| `3` | Show details for conversation #3 |
| `v 3` | Read full messages of conversation #3 |
| `r 3` | Resume conversation #3 in Claude Code |
| `b 3` | Toggle bookmark on conversation #3 |
| `s flutter` | Search for "flutter" |
| `a` | Clear search, show all |
| `n` / `p` | Next / previous page |
| `h` | Help |
| `q` | Quit |

Session ID prefixes work as targets: `r 4925f6c7`

### Non-interactive Usage

```bash
claude-conversations-cli --list
claude-conversations-cli --search "database migration" --list
claude-conversations-cli --view 4925f6c7
claude-conversations-cli --list --limit 10
```

### Resuming Conversations

```bash
claude-conversations-cli --resume 4925f6c7
```

Runs `claude --resume <session-id>`. Requires Claude Code CLI on your PATH.

---

## Update Notifications

The tool checks for new versions on PyPI at most once per hour. When an update is available:

- **Web UI** — a banner appears at the top of the page with the update command.
- **CLI** — a one-line notice appears after the welcome banner.

To update manually:

```bash
ccv --update
# or
pip3 install --upgrade claude-chats-and-analytics-viewer
```

---

## How It Works

```
~/.claude/projects/
    <project-slug>/
        <session-id>.jsonl     ← Claude Code writes these

        ↓

claude-chats-and-analytics-viewer scans & parses (cached)

        ↓

Web UI (localhost:5005)  or  CLI (terminal)
```

All data stays local — nothing is sent anywhere.

### API endpoints (Web UI)

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Serves the single-page application |
| `/api/conversations` | GET | All conversation metadata + project list |
| `/api/conversation/<id>` | GET | Full messages for a conversation |
| `/api/export/<id>?format=md\|json` | GET | Download as markdown or JSON |
| `/api/export-all` | GET | Download all as a zip archive |
| `/api/stats` | GET | Aggregate usage statistics |
| `/api/search?q=<query>` | GET | Full-text content search |
| `/api/bookmarks` | GET / POST | Get or toggle bookmarks |
| `/api/status` | GET | Server heartbeat for auto-refresh |
| `/api/update-check` | GET | `{"update_available": true/false, ...}` |

---

## Project Structure

```
claude-chats-and-analytics-viewer/
    claude_conversation_viewer/
        __init__.py           # version string
        web.py                # Web UI server + embedded HTML/CSS/JS
        cli.py                # Terminal CLI
        update_checker.py     # PyPI update checker
    .github/workflows/
        publish.yml           # Auto-publish to PyPI on version tag
    pyproject.toml            # Package metadata
    setup.cfg                 # setuptools config
    README.md
    DOCS.md                   # This file
```

---

## Troubleshooting

### "No conversations found"

```bash
ls ~/.claude/projects/
find ~/.claude/projects -name "*.jsonl" | head -5
```

### Port already in use

```bash
ccv --port 8080
# or kill the existing process:
lsof -i :5005 && kill <PID>
```

### Commands not found after pip3 install

```bash
python3 -m site --user-base
export PATH="$(python3 -m site --user-base)/bin:$PATH"
```

Or run via module:

```bash
python3 -m claude_conversation_viewer.web
python3 -m claude_conversation_viewer.cli
```

### Windows notes

- Conversation files are at `%USERPROFILE%\.claude\projects\`
- ANSI colors require Windows 10 1607+ or Windows Terminal
- `--install` is macOS-only; `--install-systemd` is Linux-only; on Windows use Task Scheduler

### Update check not working

Requires network access to `pypi.org`. Fails silently by design — never blocks startup. Manually run `ccv --update` instead.

---

## License

MIT — [github.com/AnshRajput/claude-chats-and-analytics-viewer](https://github.com/AnshRajput/claude-chats-and-analytics-viewer)
