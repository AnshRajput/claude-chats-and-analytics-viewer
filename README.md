# Claude Code Conversation Viewer

Browse, search, and resume your [Claude Code](https://docs.anthropic.com/en/docs/claude-code) conversations -- from the browser or the terminal.

![Python 3.7+](https://img.shields.io/badge/python-3.7%2B-blue)
![No Dependencies](https://img.shields.io/badge/dependencies-none-green)
![Cross Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey)
[![PyPI](https://img.shields.io/pypi/v/claude-conversation-viewer)](https://pypi.org/project/claude-conversation-viewer/)

## Features

- **Conversation browser** -- lists all past conversations with search, project filter, and sort
- **Full conversation viewer** -- chat-style display with markdown rendering and syntax highlighting
- **Resume conversations** -- jump back into any conversation with `claude --resume`
- **Export** -- download any conversation as `.md` or `.json`
- **Usage stats** -- token counts, model usage breakdown, conversations per project
- **Two interfaces** -- Web GUI and Terminal CLI, both zero-dependency
- **Cross-platform** -- auto-detects `~/.claude/` on macOS/Linux and `%USERPROFILE%\.claude\` on Windows
- **Update notifications** -- automatically checks for new versions and prompts to update

## Quick Start

### Install from PyPI

```bash
pip install claude-conversation-viewer
```

### Web UI

```bash
claude-conversations
```

Opens a browser-based GUI at `http://127.0.0.1:5005` with search, filters, stats dashboard, and export.

```
claude-conversations --port 8080    # custom port
claude-conversations --no-open      # don't auto-open browser
```

### CLI

```bash
claude-conversations-cli                          # interactive browser
claude-conversations-cli --list                   # print all conversations
claude-conversations-cli --search "auth"          # search by keyword
claude-conversations-cli --project "myproject"    # filter by project
claude-conversations-cli --view <session-id>      # view full conversation
claude-conversations-cli --resume <session-id>    # resume in Claude Code
```

In interactive mode:

| Command | Action |
|---------|--------|
| `#number` | View conversation details + session ID |
| `v #number` | View full messages |
| `r #number` | Resume conversation in Claude Code |
| `s <query>` | Search conversations |
| `n` / `p` | Next / previous page |
| `q` | Quit |

Partial session IDs work too -- `--view 4925f6c7` matches `4925f6c7-35f2-4340-bad6-ad59d4d724ee`.

### Install from source

```bash
git clone https://github.com/eazyapp-tech/claude-conversation-viewer.git
cd claude-conversation-viewer
pip install .
```

Or run directly without installing:

```bash
python3 claude_conversation_viewer.py          # Web UI
python3 claude_conversations_cli.py            # CLI
```

## Update

```bash
pip install --upgrade claude-conversation-viewer
```

The tool automatically checks for updates and shows a notification when a new version is available.

## Requirements

- Python 3.7+ (standard library only -- no external dependencies)
- Existing Claude Code conversations in `~/.claude/projects/`
- Claude Code CLI installed (for `--resume`)

## How It Works

Claude Code stores conversations as JSONL files in `~/.claude/projects/<project>/<session-id>.jsonl`. These tools:

1. Scan all project directories for conversation files
2. Parse metadata (title, timestamps, models, token usage) from each file
3. Present them in a browsable interface (web or terminal)
4. All data stays local -- nothing is sent anywhere

## License

MIT
