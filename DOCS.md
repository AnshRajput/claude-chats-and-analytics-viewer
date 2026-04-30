# Claude Code Conversation Viewer -- Installation & Usage Guide

Complete documentation for installing, configuring, and using the Claude Code Conversation Viewer.

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
- [Publishing to PyPI](#publishing-to-pypi)
- [License](#license)

---

## Overview

Claude Code Conversation Viewer lets you browse, search, export, and resume your [Claude Code](https://docs.anthropic.com/en/docs/claude-code) conversation history. It provides two interfaces:

- **Web UI** -- a browser-based GUI with search, filters, markdown rendering, syntax highlighting, export, and a usage stats dashboard.
- **CLI** -- a terminal-based interactive browser with colored output, box-drawing, search, pagination, and direct resume into Claude Code.

Both interfaces are zero-dependency (Python standard library only), cross-platform (macOS, Windows, Linux), and keep all data local.

---

## Prerequisites

| Requirement | Details |
|---|---|
| **Python** | 3.7 or later |
| **Claude Code** | Installed and used at least once (so `~/.claude/projects/` exists with conversation files) |
| **pip** | For PyPI installation (included with Python 3.4+) |
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

The simplest method -- works for anyone, no repo access required:

```bash
pip install claude-conversation-viewer
```

This installs two commands:

| Command | Description |
|---|---|
| `claude-conversations` | Starts the Web UI |
| `claude-conversations-cli` | Starts the CLI |

> **Note:** If pip installs scripts to a directory not on your PATH (e.g., `~/.local/bin` on Linux or `~/Library/Python/3.x/bin` on macOS), you may need to add it:
> ```bash
> # Linux/macOS (add to your shell profile)
> export PATH="$HOME/.local/bin:$PATH"
>
> # Or run directly via Python
> python3 -m claude_conversation_viewer.web
> python3 -m claude_conversation_viewer.cli
> ```

### Install from Source

Clone the repository and install locally:

```bash
git clone https://github.com/eazyapp-tech/claude-conversation-viewer.git
cd claude-conversation-viewer
pip install .
```

Or run directly without installing (from the repo root):

```bash
python3 claude_conversation_viewer.py          # Web UI
python3 claude_conversations_cli.py            # CLI
```

### Verify Installation

```bash
claude-conversations --help
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
claude-conversations
```

This starts a local HTTP server and opens your default browser to `http://127.0.0.1:5005`.

On startup, the viewer scans all conversation files and loads metadata (titles, timestamps, models, token counts). This is a one-time scan per server start.

### Web UI Command-line Options

| Flag | Default | Description |
|---|---|---|
| `--port PORT` | `5005` | Port to serve on |
| `--no-open` | Off | Don't auto-open the browser |
| `--install` | -- | Install as a macOS LaunchAgent (auto-start on login) |
| `--uninstall` | -- | Remove the macOS LaunchAgent |

Examples:

```bash
claude-conversations --port 8080           # custom port
claude-conversations --no-open             # headless / server mode
claude-conversations --port 9000 --no-open # both
```

### Navigating the Interface

The Web UI has two panels:

- **Sidebar (left)** -- conversation list with search, project filter, and sort controls.
- **Main panel (right)** -- conversation viewer or stats dashboard.

Two tabs at the top of the sidebar switch between:

- **Conversations** -- the browsable conversation list
- **Stats** -- the usage statistics dashboard

### Searching and Filtering

The sidebar provides three controls:

| Control | Description |
|---|---|
| **Search box** | Filters conversations by title, project path, or model name. Real-time filtering as you type. |
| **Project filter** | Dropdown to show only conversations from a specific project. Shows shortened project paths. |
| **Sort order** | Sort by: Newest first (default), Oldest first, Most messages, Most tokens |

All three controls can be combined. For example, filter to a specific project, search for "auth", and sort by most messages.

### Viewing a Conversation

Click any conversation in the sidebar to view it. The main panel shows:

- **Header** -- conversation title, export buttons
- **Session bar** -- full session ID, "Copy resume command" button
- **Messages** -- full conversation in chat-style layout with:
  - User messages (blue-tinted background)
  - Assistant messages (dark background with purple role label)
  - Tool use blocks (collapsible, showing tool name and input)
  - Tool result blocks (collapsible, showing output)
  - Markdown rendering with syntax-highlighted code blocks
  - Token usage badges per assistant message

On mobile devices, the sidebar and main panel switch to a stacked layout with a back button.

### Exporting Conversations

Two export formats are available via buttons in the conversation header:

| Format | Description |
|---|---|
| **Export .md** | Markdown file with conversation metadata header and all messages. Tool use/results are in `<details>` blocks. |
| **Export .json** | JSON file with full metadata and structured message content. |

Files are downloaded as `<session-id>.md` or `<session-id>.json`.

### Usage Statistics Dashboard

Click the **Stats** tab to see:

- **Summary cards** -- total conversations, projects, messages, tokens (input, output, cache creation, cache read)
- **Model usage chart** -- horizontal bar chart showing conversation count per model
- **Top projects chart** -- horizontal bar chart showing conversation count per project (top 10)

### Background Service (macOS)

Run the Web UI as a persistent background service that auto-starts on login:

```bash
claude-conversations --install                   # install with default port 5005
claude-conversations --install --port 8080       # install with custom port
```

This creates a macOS LaunchAgent at `~/Library/LaunchAgents/com.claude-conversation-viewer.plist`. The service:

- Starts automatically on login
- Runs with `--no-open` (no browser popup on login)
- Logs to `/tmp/claude-conversation-viewer.log`

To remove the service:

```bash
claude-conversations --uninstall
```

To check service status:

```bash
launchctl list | grep claude-conversation
```

**Linux alternative:** Add to crontab manually:

```bash
crontab -e
# Add this line:
@reboot python3 -m claude_conversation_viewer.web --port 5005 --no-open &
```

---

## CLI

### Interactive Mode

```bash
claude-conversations-cli
```

Launches an interactive terminal browser with a styled welcome banner, paginated conversation list, search, and conversation viewer.

### CLI Command-line Options

| Flag | Description |
|---|---|
| `--list` | Print conversations non-interactively (pipe-friendly) |
| `--search QUERY` | Filter conversations by keyword |
| `--project NAME` | Filter by project name |
| `--view SESSION_ID` | View a specific conversation (full or partial ID) |
| `--resume SESSION_ID` | Resume a conversation in Claude Code |
| `--limit N` | Max conversations in `--list` mode (default: 50) |

Flags can be combined:

```bash
claude-conversations-cli --search "auth" --project "myapp" --list
```

### Interactive Commands

When in interactive mode, the following commands are available at the prompt:

| Command | Action |
|---|---|
| `3` | Show details for conversation #3 (metadata card with session ID, project, date, tokens, resume command) |
| `v 3` | Read full messages of conversation #3 |
| `r 3` | Resume conversation #3 in Claude Code (prompts for confirmation, then runs `claude --resume <id>`) |
| `s flutter` | Search all conversations for "flutter" (enters search sub-mode) |
| `a` | Clear search, return to all conversations |
| `n` | Next page |
| `p` | Previous page |
| `h` | Show help |
| `q` | Quit |

Session ID prefixes also work as targets:

```
r 4925f6c7          # resume by partial session ID
v 4925f6c7          # view by partial session ID
```

### Non-interactive Usage

For scripting or quick lookups:

```bash
# List recent conversations
claude-conversations-cli --list

# Search and list
claude-conversations-cli --search "database migration" --list

# View a specific conversation
claude-conversations-cli --view 4925f6c7

# View with full session ID
claude-conversations-cli --view 4925f6c7-35f2-4340-bad6-ad59d4d724ee

# Limit output
claude-conversations-cli --list --limit 10
```

### Resuming Conversations

Resume jumps directly into Claude Code with the selected conversation context:

```bash
# Via CLI flag
claude-conversations-cli --resume 4925f6c7

# Via interactive mode
# Type: r 3  (for conversation #3)
# Confirm with: y
```

This runs `claude --resume <session-id>` and replaces the current process. Requires the Claude Code CLI to be installed and on your PATH.

---

## Update Notifications

The tool automatically checks for new versions on PyPI (at most once per hour, cached). When an update is available:

- **Web UI** -- a purple banner appears at the top of the page:
  > "Update available! Run `pip install --upgrade claude-conversation-viewer` to update."
  
  Click the X button to dismiss (stays dismissed for the browser tab session).

- **CLI** -- a one-line notice appears after the welcome banner or after `--list` output:
  > "Update available! Run: pip install --upgrade claude-conversation-viewer"

### Update check details

- Queries `https://pypi.org/pypi/claude-conversation-viewer/json` for the latest version
- Compares against the installed version using semantic version comparison
- Results are cached in a temp file (`/tmp/claude-viewer-update-check`) for 1 hour
- Cache is shared between Web UI and CLI
- Runs in a background thread (CLI) or via async fetch (Web UI) -- never blocks startup
- Fails silently on network errors, missing PyPI package, or any other issue

### Updating

```bash
pip install --upgrade claude-conversation-viewer
```

---

## How It Works

### Data flow

```
~/.claude/projects/
    <project-slug>/
        <session-id>.jsonl     <-- Claude Code writes these
        <session-id>.jsonl
    <another-project>/
        <session-id>.jsonl
        
        |
        v

claude-conversation-viewer scans & parses

        |
        v

Web UI (localhost:5005)  or  CLI (terminal)
```

### JSONL format

Each `.jsonl` file contains one JSON object per line. Key fields:

```json
{
  "type": "user",
  "message": {
    "role": "user",
    "content": "the user's message"
  },
  "timestamp": "2025-04-23T10:30:00.000Z",
  "cwd": "/Users/you/project",
  "version": "1.0.30"
}
```

```json
{
  "type": "assistant",
  "message": {
    "role": "assistant",
    "model": "claude-sonnet-4-20250514",
    "content": [{"type": "text", "text": "response..."}],
    "usage": {
      "input_tokens": 1234,
      "output_tokens": 567,
      "cache_creation_input_tokens": 890,
      "cache_read_input_tokens": 432
    }
  },
  "timestamp": "2025-04-23T10:30:05.000Z"
}
```

The viewer extracts metadata (title from first user message, timestamps, models, token totals) for the list view, and parses full content for the conversation viewer.

---

## Project Structure

```
claude-conversation-viewer/
    claude_conversation_viewer/       # Python package
        __init__.py                   # version string
        web.py                        # Web UI server + embedded HTML/CSS/JS
        cli.py                        # Terminal CLI
        update_checker.py             # PyPI update checker
    claude_conversation_viewer.py     # Backward-compatible wrapper (Web UI)
    claude_conversations_cli.py       # Backward-compatible wrapper (CLI)
    pyproject.toml                    # PEP 621 package metadata
    setup.cfg                         # setuptools config (pip < 22 compat)
    setup.py                          # minimal setup.py (pip < 22 compat)
    README.md                         # Project readme
    DOCS.md                           # This file
```

### Entry points

| Installed command | Maps to |
|---|---|
| `claude-conversations` | `claude_conversation_viewer.web:main()` |
| `claude-conversations-cli` | `claude_conversation_viewer.cli:main()` |

### API endpoints (Web UI)

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Serves the HTML single-page application |
| `/api/conversations` | GET | Returns all conversation metadata + project list |
| `/api/conversation/<id>` | GET | Returns full messages for a conversation |
| `/api/export/<id>?format=md\|json` | GET | Downloads conversation as markdown or JSON |
| `/api/stats` | GET | Returns aggregate usage statistics |
| `/api/update-check` | GET | Returns `{"update_available": true/false, ...}` |

---

## Troubleshooting

### "No conversations found"

- Ensure Claude Code has been used at least once in a project
- Check that `~/.claude/projects/` exists and contains `.jsonl` files:
  ```bash
  ls ~/.claude/projects/
  find ~/.claude/projects -name "*.jsonl" | head -5
  ```

### Port already in use

```bash
claude-conversations --port 8080
```

Or find and kill the existing process:

```bash
lsof -i :5005
kill <PID>
```

### Commands not found after pip install

If `claude-conversations` is not found, pip likely installed to a directory not on your PATH:

```bash
# Find where pip installed the scripts
python3 -m site --user-base
# Add the bin directory to PATH
export PATH="$(python3 -m site --user-base)/bin:$PATH"
```

Or run via Python module:

```bash
python3 -m claude_conversation_viewer.web
python3 -m claude_conversation_viewer.cli
```

### Windows-specific notes

- Conversation files are located at `%USERPROFILE%\.claude\projects\`
- ANSI colors in the CLI require Windows 10 1607+ or Windows Terminal
- The `--install` background service flag is macOS-only; on Windows, use Task Scheduler instead

### Update check not working

The update check requires network access to `pypi.org`. If you're behind a firewall or proxy, the check silently fails and no banner is shown. This is by design -- it never blocks or errors.

To manually check for updates:

```bash
pip install --upgrade claude-conversation-viewer
```

---

## Publishing to PyPI

For maintainers -- steps to publish a new release:

### 1. Bump version

Update the version in three places:

- `claude_conversation_viewer/__init__.py`
- `pyproject.toml`
- `setup.cfg`

### 2. Build the distribution

```bash
pip install build twine
python3 -m build
```

This creates `dist/claude_conversation_viewer-X.Y.Z.tar.gz` and `dist/claude_conversation_viewer-X.Y.Z-py3-none-any.whl`.

### 3. Upload to PyPI

```bash
# Test upload first
python3 -m twine upload --repository testpypi dist/*

# Production upload
python3 -m twine upload dist/*
```

You'll need a PyPI account and API token. Configure in `~/.pypirc`:

```ini
[pypi]
username = __token__
password = pypi-<your-api-token>
```

### 4. Verify

```bash
pip install --upgrade claude-conversation-viewer
python3 -c "from claude_conversation_viewer import __version__; print(__version__)"
```

---

## License

MIT
