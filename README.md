# Claude Code Conversation Viewer

Browse, search, and resume your [Claude Code](https://docs.anthropic.com/en/docs/claude-code) conversations — from the browser or terminal.

![Python 3.7+](https://img.shields.io/badge/python-3.7%2B-blue)
![No Dependencies](https://img.shields.io/badge/dependencies-none-green)
![Cross Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey)

---

## One-command install & run

```bash
pip install claude-conversation-viewer && ccv
```

That's it. Opens `http://127.0.0.1:5005` in your browser.

### Alternatives

```bash
# With pipx (no permanent install)
pipx run claude-conversation-viewer

# With uv (fastest, no install needed)
uvx claude-conversation-viewer

# Shell script (auto install + launch)
curl -fsSL https://raw.githubusercontent.com/AnshRajput/Claude-Conversation-viewer/main/install.sh | sh
```

---

## Features

- **Conversation browser** — search, filter by project, sort by date / tokens / cost
- **Cost estimation** — shows `~$0.04` per conversation and per message based on model pricing
- **Deep content search** — toggle `DEEP` to search inside every message, not just titles
- **Bookmarks** — star any conversation, persisted to `~/.claude/viewer-bookmarks.json`
- **Activity heatmap** — GitHub-style 52-week calendar in the Stats tab
- **Copy buttons** — one-click copy on every code block
- **Auto-refresh** — detects new conversations while the viewer is open
- **Export** — download as `.md` or `.json`, or export all as a `.zip`
- **Smart caching** — instant startup after first run (only re-parses changed files)
- **Usage stats** — token counts, model breakdown, top projects, total estimated cost
- **CLI** — terminal interface with search, pagination, and resume
- **Keyboard shortcuts** — `/` search · `j`/`k` navigate · `b` bookmark · `Enter` open
- **Zero dependencies** — Python stdlib only, works on macOS / Windows / Linux

---

## Commands

| Command | What it does |
|---------|-------------|
| `ccv` | Start Web UI |
| `ccv --update` | Update to latest version |
| `ccv --port 8080` | Custom port |
| `ccv --no-open` | Don't auto-open browser |
| `ccv --install` | Auto-start on login (macOS) |
| `ccv --install-systemd` | Auto-start on login (Linux) |
| `claude-conversations-cli` | Terminal CLI |

---

## CLI

```bash
claude-conversations-cli                       # interactive browser
claude-conversations-cli --search "auth"       # search by keyword
claude-conversations-cli --view <session-id>   # view a conversation
claude-conversations-cli --resume <session-id> # resume in Claude Code
```

---

## How it works

Claude Code saves conversations as JSONL files in `~/.claude/projects/<project>/<session-id>.jsonl`.
This tool scans those files, caches metadata, and serves a fast local web UI.
**All data stays local — nothing is sent anywhere.**

---

## Update

```bash
ccv --update
```

---

## License

MIT — [github.com/AnshRajput/Claude-Conversation-viewer](https://github.com/AnshRajput/Claude-Conversation-viewer)
