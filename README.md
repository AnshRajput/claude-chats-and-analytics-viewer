# Ledger

> **Claude Code accounts.** Your conversations, cost, and waste — in one private ledger.

[![PyPI](https://img.shields.io/pypi/v/claude-chats-and-analytics-viewer?color=E3FF70&label=pypi)](https://pypi.org/project/claude-chats-and-analytics-viewer/)
[![Python 3.7+](https://img.shields.io/badge/python-3.7%2B-black)](https://python.org)
[![Zero dependencies](https://img.shields.io/badge/dependencies-none-22c55e)](https://pypi.org/project/claude-chats-and-analytics-viewer/)
[![Cross platform](https://img.shields.io/badge/macOS%20%7C%20Linux%20%7C%20Windows-black)](#install)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

Ledger is a local browser + terminal UI for every Claude Code conversation on your disk. It reads the JSONL sessions Claude Code writes to `~/.claude/projects/`, and gives you a browser, a CLI, and a full **Dashboard** — token usage, cost, activity, waste patterns, model comparison, and git-correlated yield.

Everything runs on your machine. No proxy, no wrapper, no API keys. Nothing leaves your disk.

---

## What you get

- **Browser & search** — every conversation you've ever had with Claude Code, with deep full-message search, project filters, bookmarks, and one-click resume.
- **Dashboard** — a CRED-style observability panel with five sub-tabs:
  - **Overview** — headline spend, cache hit rate, daily cost chart, 52-week activity heatmap, 13-category task classifier, top models, projects, tools, shell commands, MCP servers, and most-expensive sessions.
  - **Optimize** — a 6-pattern waste scanner with A–F health grade and copy-paste fixes (duplicate reads, low Read:Edit ratio, cache-creation overhead, junk directory reads, uncapped bash output, bloated `CLAUDE.md`).
  - **Compare** — side-by-side model performance: one-shot rate, retry rate, self-correction, cost per call / edit, output tokens / call, cache hit, delegation, planning, tools per turn.
  - **Yield** — correlates sessions with `git log` in each project and labels them *productive*, *reverted*, or *abandoned*.
  - **Plan** — subscription tracking for Claude Max / Pro / Cursor Pro / custom monthly budgets.
- **Export** — per-conversation `.md` / `.json`, zip of all conversations, full dashboard CSV/JSON.
- **Terminal CLI** — paginated interactive browser with keyboard navigation, search, bookmarks, and direct resume into Claude Code.
- **Zero dependencies.** Python standard library only.

---

## Install

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/AnshRajput/claude-chats-and-analytics-viewer/main/install.sh | sh
```

### Windows (PowerShell)

```powershell
iwr https://raw.githubusercontent.com/AnshRajput/claude-chats-and-analytics-viewer/main/install.ps1 | iex
```

### Manual

```bash
pipx install claude-chats-and-analytics-viewer      # recommended
uvx claude-chats-and-analytics-viewer               # uv, no install
pip3 install --user claude-chats-and-analytics-viewer
```

Don't have `pipx`?
- macOS: `brew install pipx && pipx ensurepath`
- Linux: `python3 -m pip install --user pipx && python3 -m pipx ensurepath`
- Windows: `pip install pipx && pipx ensurepath`

Run a new terminal after `ensurepath` and type `ccv`.

---

## Run

```bash
ccv                        # start Ledger (opens browser)
ccv --port 8080            # use a different port
ccv --no-open              # start without opening the browser
ccv --update               # update to latest
ccv --install              # auto-start on login (macOS LaunchAgent)
ccv --install-systemd      # auto-start on login (Linux systemd)
ccv --uninstall            # remove the auto-start service
```

Terminal CLI:

```bash
ccvc                              # interactive browser
ccvc -v                           # version
ccvc --check-update               # check PyPI for a newer version
ccvc --search "auth bug"          # filter by keyword
ccvc --project "myapp"            # filter by project
ccvc --view <session-id>          # view a conversation
ccvc --resume <session-id>        # resume in Claude Code
ccvc --list --limit 10            # non-interactive list
```

### Keyboard shortcuts (CLI)

| Key | Action |
|---|---|
| `3` | Details for conversation #3 |
| `v 3` | Read the full messages of #3 |
| `r 3` | Resume #3 in Claude Code |
| `b 3` | Toggle bookmark on #3 |
| `s <query>` | Search |
| `a` | Clear search |
| `n` / `p` | Next / previous page |
| `q` | Quit |

---

## Dashboard API

The web UI is a thin layer over a local JSON API you can script against:

| Endpoint | Returns |
|---|---|
| `GET /api/dashboard?period=<today\|7d\|30d\|month\|all\|custom>&from=&to=` | Overview, daily chart, projects, models, activities (13 categories with one-shot rate), core tools, shell commands, MCP servers, top sessions |
| `GET /api/dashboard/optimize?period=...` | Waste-pattern findings with impact, tokens saved, and copy-paste fixes; A–F health grade |
| `GET /api/dashboard/compare?period=...&models=a,b` | Per-model performance + efficiency + behavior metrics |
| `GET /api/dashboard/yield?period=...&project=...` | Session outcomes correlated with `git log` (productive / reverted / abandoned / no-git) |
| `GET /api/dashboard/plan` / `POST /api/dashboard/plan` | Read or set your monthly plan (`claude-max`, `claude-pro`, `cursor-pro`, `custom`, `none`) |
| `GET /api/dashboard/export?format=csv\|json&period=...` | Downloadable multi-period export |

Every endpoint respects the selected period and stays 100% local.

---

## How the task classifier works

Each assistant turn is assigned to one of 13 categories by matching tools used + keywords in your message (no LLM calls, deterministic):

| Category | Triggered by |
|---|---|
| coding | Edit / Write / NotebookEdit tools |
| debugging | coding turn + "fix / bug / error / broken / traceback" keywords |
| feature | coding turn + "add / create / implement / new" |
| refactoring | coding turn + "refactor / rename / simplify" |
| testing | Bash running pytest / vitest / jest / mocha / coverage |
| exploration | Read / Grep / Glob / WebSearch / MCP only, no edits |
| planning | `EnterPlanMode` used, or TaskCreate without edits |
| delegation | Agent / Task tool spawned |
| git | Bash with `git push/commit/merge/rebase/...` |
| build/deploy | `npm run build`, `docker`, `pm2`, `systemctl`, etc. |
| brainstorming | No tools, message about "idea / what if / strategy" |
| conversation | No tools, pure dialogue |
| general | Skill tool / uncategorized |

From this the dashboard computes **one-shot rate** per category (the share of edit turns that didn't need a retry) — the cleanest signal for how well Claude got it right on the first try.

---

## How it works

```
~/.claude/projects/<project-slug>/<session-id>.jsonl
                           │
                           ▼
  parser.py  →  per-turn data (tools, tokens, model, classifier)
                           │
                           ▼
  store.py   →  mtime-keyed metadata cache (v3, tmpdir)
                           │
                           ▼
  web.py  ←  HTTP server  →  single-page Dashboard UI
  cli.py  ←  terminal UI
```

Claude Code writes session transcripts as JSONL. Ledger reads them, parses per-turn usage + tool calls, caches the result, and serves everything from a local HTTP server on `127.0.0.1:5005`. Nothing is uploaded.

---

## Project structure

```
claude_conversation_viewer/
├── __init__.py              # __version__
├── web.py                   # HTTP handler + single-file HTML/CSS/JS UI
├── cli.py                   # Interactive terminal CLI
├── update_checker.py        # PyPI update check
├── pricing.py               # Model pricing table + cost helpers
├── classifier.py            # 13-category deterministic classifier
├── parser.py                # JSONL parsing (metadata + per-turn + full messages)
├── cache.py                 # Metadata cache v3, bookmarks, plan storage
├── store.py                 # ConversationStore
└── dashboard/
    ├── aggregator.py        # /api/dashboard payload
    ├── optimize.py          # Waste detectors + A–F grade
    ├── compare.py           # Model comparison
    ├── yield_tracker.py     # Git-correlated yield
    ├── plans.py             # Subscription presets
    ├── export.py            # CSV / JSON export
    └── period.py            # Period parsing
```

---

## Troubleshooting

**`ccv` not found after install**
Run `pipx ensurepath` and open a new terminal.

**Port already in use**
Ledger automatically falls through to the next available port and prints it; or specify `ccv --port 8080`.

**No conversations found**
Make sure you've run Claude Code at least once. Check:
```bash
ls ~/.claude/projects/
find ~/.claude/projects -name "*.jsonl" | head
```

**Update check not working**
Requires network access to `pypi.org`. Fails silently by design.

---

## Credits

- Dashboard observability inspired by [codeburn](https://github.com/getagentseal/codeburn) — the terminal-first sibling that tracks the same data across 16 AI coding tools.
- Visual language: CRED-style premium minimalism — warm black neutrals, Inter Tight, JetBrains Mono, one chartreuse accent.

## License

MIT — [github.com/AnshRajput/claude-chats-and-analytics-viewer](https://github.com/AnshRajput/claude-chats-and-analytics-viewer)
