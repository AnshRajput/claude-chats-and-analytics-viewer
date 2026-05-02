# Ledger — Documentation

Browse, search, export, and resume your [Claude Code](https://docs.anthropic.com/en/docs/claude-code) conversations — and see where every token and dollar went — from the browser or terminal.

---

## Table of contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
  - [macOS](#macos)
  - [Linux](#linux)
  - [Windows](#windows)
  - [Alternatives](#alternatives-no-pipx-needed)
  - [Install from source](#install-from-source)
  - [Verify installation](#verify-installation)
- [Web UI](#web-ui)
  - [Starting the server](#starting-the-server)
  - [Command-line options](#web-ui-command-line-options)
  - [Navigating the interface](#navigating-the-interface)
  - [Searching and filtering](#searching-and-filtering)
  - [Viewing a conversation](#viewing-a-conversation)
  - [Exporting conversations](#exporting-conversations)
  - [Bookmarks](#bookmarks)
  - [Settings](#settings)
  - [Background service](#background-service)
- [Dashboard](#dashboard)
  - [Period selector](#period-selector)
  - [Overview](#overview-1)
  - [Optimize — waste scanner](#optimize--waste-scanner)
  - [Compare — side-by-side models](#compare--side-by-side-models)
  - [Yield — productive vs reverted vs abandoned](#yield--productive-vs-reverted-vs-abandoned)
  - [Plan — subscription tracking](#plan--subscription-tracking)
  - [Export CSV / JSON](#export-csv--json)
- [Task classifier](#task-classifier)
- [Optimize detectors](#optimize-detectors)
- [CLI](#cli)
- [Update notifications](#update-notifications)
- [HTTP API](#http-api)
- [How it works](#how-it-works)
- [Project structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Overview

Ledger is a local browser + terminal UI over your Claude Code conversation history. It has two interfaces:

- **Web UI** (`ccv`) — a single-page app with conversation browser, deep search, bookmarks, export, and a full observability **Dashboard** (Overview, Optimize, Compare, Yield, Plan).
- **CLI** (`ccvc`) — a terminal-based interactive browser with colored output, pagination, search, and direct resume into Claude Code.

Both are zero-dependency (Python standard library only), cross-platform (macOS, Linux, Windows), and **entirely local** — no proxy, no wrapper, no API keys. Session data never leaves your disk.

---

## Prerequisites

| Requirement | Details |
|---|---|
| **Python** | 3.7 or later |
| **Claude Code** | Installed and used at least once (so `~/.claude/projects/` exists with `.jsonl` session files) |
| **pipx** (recommended) | For installing Python CLI apps cleanly; `pip3` or `uvx` also work |
| **Claude Code CLI** | Required only for the `--resume` / `r` command to jump back into a conversation |
| **git** | Optional — enables the Yield tab to correlate sessions with commits |

### Where Claude Code stores conversations

Claude Code saves each conversation as a JSONL file at:

```
~/.claude/projects/<project-slug>/<session-id>.jsonl       # macOS, Linux
%USERPROFILE%\.claude\projects\<project-slug>\<session-id>.jsonl   # Windows
```

Ledger auto-detects the correct path on each platform. Override via the `CLAUDE_CONFIG_DIR` environment variable (future-proofed; same convention as other Claude Code tools).

---

## Installation

### macOS

```bash
brew install pipx && pipx ensurepath          # once
# open a new terminal, then:
pipx install claude-chats-and-analytics-viewer
ccv
```

> Homebrew Python enforces PEP 668 and blocks `pip install`. `pipx` is the correct tool for installing Python CLI apps on macOS.

### Linux

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
# open a new terminal, then:
pipx install claude-chats-and-analytics-viewer
ccv
```

On Ubuntu/Debian you can also use `sudo apt install pipx`.

### Windows

```powershell
pip install pipx
pipx ensurepath
# open a new terminal, then:
pipx install claude-chats-and-analytics-viewer
ccv
```

Install Python from [python.org](https://python.org) and tick **Add to PATH** during setup.

### Alternatives (no pipx needed)

```bash
uvx claude-chats-and-analytics-viewer                     # uv — runs without installing
pip3 install --user claude-chats-and-analytics-viewer     # user-site install
```

### Install from source

```bash
git clone https://github.com/AnshRajput/claude-chats-and-analytics-viewer.git
cd claude-chats-and-analytics-viewer
pipx install .
```

### Installed commands

| Command | What it does |
|---|---|
| `ccv` | Start the web UI |
| `ccvc` | Start the terminal CLI |
| `claude-conversations` | Web UI (alias) |
| `claude-dashboard` | Web UI (alias) |
| `claude-conversations-cli` | CLI (alias) |

### Verify installation

```bash
ccv --help
ccvc --help
ccvc -v                                  # prints 3.0.0 (or later)
python3 -c "from claude_conversation_viewer import __version__; print(__version__)"
```

---

## Web UI

### Starting the server

```bash
ccv
```

Opens `http://127.0.0.1:5005` in your default browser. On startup Ledger scans every JSONL session in `~/.claude/projects/`, parses both aggregate metadata and per-turn data, and caches the results (version-3 cache, keyed by mtime + size). Cold startup on the first run may take a few seconds; every subsequent run is near-instant.

### Web UI command-line options

| Flag | Default | Description |
|---|---|---|
| `--port PORT` | `5005` | Port to serve on (falls back to a free one if taken) |
| `--no-open` | off | Don't auto-open the browser |
| `--update` | — | Upgrade to the latest version via pipx / uv / pip |
| `--install` | — | Install as a macOS LaunchAgent (auto-start on login) |
| `--install-systemd` | — | Install as a Linux systemd user service |
| `--uninstall` | — | Remove the macOS LaunchAgent |

Examples:

```bash
ccv --port 8080           # custom port
ccv --no-open             # headless / server mode
ccv --update              # upgrade from PyPI
```

### Navigating the interface

Ledger has two panels:

- **Sidebar** (left) — conversation list with search, project filter, sort, and four tabs at the top:
  - **Conversations** — browsable list, default tab
  - **Saved** — bookmarked conversations
  - **Dashboard** — full observability panel (see below)
  - **Settings** — version info, update check, cache directory
- **Main panel** (right) — the selected conversation, or the Dashboard / Settings panel

### Searching and filtering

| Control | Description |
|---|---|
| **Search box** | Matches titles, project paths, and models. Press `/` from anywhere to focus it. |
| **DEEP toggle** | Switch to full-text search inside every message (slower, scans every `.jsonl` file) |
| **Project filter** | Dropdown showing every project — narrow the list to one |
| **Sort** | Newest, Oldest, Most messages, Most tokens, Highest cost |

Keyboard shortcuts (web):

| Key | Action |
|---|---|
| `/` | Focus search |
| `j` / `k` | Move selection down / up |
| `↵` | Open the selected conversation |
| `b` | Bookmark the selected conversation |

### Viewing a conversation

Click any row to open it. The main panel shows:

- **Header** — title, total cost badge, star (bookmark) button, `.md` / `.json` export buttons
- **Session bar** — full session ID + **Copy resume cmd** button (copies `claude --resume <id>`)
- **Messages** — user / assistant / thinking blocks with collapsible tool use and tool result, syntax-highlighted code with per-block copy buttons, and per-turn token / cost badges

### Exporting conversations

| Format | Description |
|---|---|
| **Export .md** | Markdown file with a metadata header and all messages |
| **Export .json** | JSON file with structured metadata + content blocks |
| **Export all (.md zip)** | ZIP of every conversation as markdown |
| **Export all (.json zip)** | ZIP of every conversation as JSON |

Ledger also exports the full Dashboard payload (see [Export CSV / JSON](#export-csv--json)).

### Bookmarks

Star a conversation to pin it. Bookmarks persist at `~/.claude/viewer-bookmarks.json`. The **Saved** tab shows them sorted by last activity.

### Settings

The **Settings** tab shows:

- Installed version
- PyPI package name
- Projects directory (e.g. `~/.claude/projects/`)
- Conversation count
- Latest PyPI version with a **Check for updates** and **Update now** button

### Background service

Auto-start Ledger on login.

**macOS**
```bash
ccv --install               # default port 5005
ccv --install --port 8080   # custom port
```
Creates a LaunchAgent at `~/Library/LaunchAgents/com.claude-conversation-viewer.plist`.

```bash
ccv --uninstall
launchctl list | grep claude-conversation    # check status
```

**Linux**
```bash
ccv --install-systemd
systemctl --user daemon-reload
systemctl --user enable claude-conversation-viewer
systemctl --user start claude-conversation-viewer
```

**Windows** — use Task Scheduler or Start-up folder.

---

## Dashboard

Click the **Dashboard** tab. The dashboard has five sub-tabs:

```
Overview   Optimize   Compare   Yield   Plan
```

### Period selector

Every sub-tab respects the same period selector:

| Period | Window |
|---|---|
| Today | Today only (UTC) |
| 7 Days | Rolling 7-day window |
| 30 Days | Rolling 30-day window |
| Month | Current calendar month |
| All Time | Every recorded session |

Add `?period=custom&from=YYYY-MM-DD&to=YYYY-MM-DD` to the API for an explicit date range.

### Overview

The default view. Top-down:

1. **Hero spend** — headline cost in large bold sans, API call / session / average-cost line.
2. **Supporting stats** — Today, This Month, Cache hit rate, Total tokens.
3. **Plan progress** (if a plan is configured) — API-equivalent spend vs the plan's monthly price.
4. **Activity** — a **GitHub-style 52-week heatmap** in 4 chartreuse levels, colored by daily cost.
5. **Daily cost** — a canvas bar chart of $ per day in the selected period.
6. **Activities & models** — 13-category classifier breakdown with per-category one-shot rate; model breakdown with tabular cost, calls, tokens.
7. **Projects & tools** — top projects by cost with `avg cost / session`; core-tools histogram (Read / Edit / Write / Bash / Grep / Glob / TodoWrite / Task / WebSearch / WebFetch / NotebookEdit).
8. **Shell & MCP** — most-used bash commands (grouped by head: `git status`, `npm run`, `docker`, …) and MCP server calls grouped by `mcp__<server>__` prefix.
9. **Expensive sessions** — the top five most-expensive sessions in the period, clickable to jump into the conversation.

### Optimize — waste scanner

Scans the selected period for six waste patterns, ranks them by impact, and assigns a setup health grade:

- **Grade bands** — A ≥ 90, B ≥ 75, C ≥ 55, D ≥ 30, F otherwise (starts at 100, subtracts 15 per *high*, 7 per *medium*, 3 per *low* finding, capped at an 80-point penalty).

Each finding carries:

- **Title** and **explanation**
- **Impact** — high / medium / low
- **Tokens saved** — estimated
- **Fix** — a copy-paste `CLAUDE.md` snippet, a shell command, or a configuration change

Every code block has a **Copy** button. See [Optimize detectors](#optimize-detectors) for the full list.

### Compare — side-by-side models

Per model in the selected period, computes:

| Section | Metric |
|---|---|
| Performance | One-shot rate (%) |
| Performance | Retry rate per edit (avg) |
| Performance | Self-correction (%) |
| Efficiency | Cost per call ($) |
| Efficiency | Cost per edit ($) |
| Efficiency | Output tokens per call |
| Efficiency | Cache hit rate (%) |
| Behavior | Delegation rate (%) |
| Behavior | Planning rate (%) |
| Behavior | Avg tools per turn |

The grid auto-sizes to the number of models — Opus, Sonnet, Haiku appear side-by-side with their call counts and total spend in the header.

### Yield — productive vs reverted vs abandoned

Correlates each session with `git log` in its `cwd`:

| Status | Meaning |
|---|---|
| **productive** | Commits were made inside the session window and remain on `HEAD` |
| **reverted** | A later `git revert` touched one of the commits |
| **abandoned** | No commits inside the session window (or project isn't a git repo) |
| **no-git** | The session's `cwd` isn't a git working tree |

Shown as a stacked bar plus a per-session table with commit counts.

Requires `git` on PATH. Run from a directory that doesn't matter — Ledger runs `git log` inside each session's `cwd`.

### Plan — subscription tracking

Choose a preset to track API-equivalent spend against your plan price:

| Preset | Monthly price |
|---|---|
| Claude Max | $200 |
| Claude Pro | $20 |
| Cursor Pro | $20 |
| Custom | Any USD/month you set |
| None | Hide the plan bar |

Stored locally at `~/.claude/viewer-plan.json`. Plan progress (`month_cost / monthly_price`) shows on the Overview as a thin bar with percent used.

> Presets reflect publicly stated plan prices, not real token allowances — vendors don't publish precise consumer-plan limits. Treat the bar as a break-even indicator, not a quota meter.

### Export CSV / JSON

Click **CSV** or **JSON** in the top-right of any sub-tab to download the dashboard payload for the current period.

- **CSV** — flat sections: Overview, Daily, Projects, Models, Activities, Core tools, Shell commands, MCP servers, Top sessions
- **JSON** — the exact `GET /api/dashboard` payload

---

## Task classifier

Each assistant turn is assigned one of 13 categories. The classifier is deterministic — regex + tool-set matching, no LLM calls — so results are fast, private, and reproducible.

| Category | Triggered by |
|---|---|
| coding | Edit / Write / NotebookEdit |
| debugging | coding + "fix / bug / error / broken / failing / crash / traceback" keywords |
| feature | coding + "add / create / implement / new / build / scaffold / generate" |
| refactoring | coding + "refactor / rename / simplify / extract / migrate / split" |
| testing | Bash running `pytest`, `vitest`, `jest`, `mocha`, `coverage`, `npm test` |
| exploration | Read / Grep / Glob / WebSearch / WebFetch / MCP only (no edits) |
| planning | `EnterPlanMode` / `ExitPlanMode` tools, or TaskCreate without edits |
| delegation | `Task`, `Agent`, or `dispatch_agent` tool |
| git | Bash with `git push/commit/merge/rebase/checkout/branch/stash/log/diff/status/add/reset/cherry-pick/tag` |
| build/deploy | `npm run build`, `npm publish`, `docker`, `pm2`, `systemctl`, `brew`, `cargo build` |
| brainstorming | No tools, message mentions "brainstorm / idea / what if / strategy / approach / design" |
| conversation | No tools, pure dialogue |
| general | Skill tool or uncategorized |

### One-shot rate

For any turn that includes edits, Ledger counts **retries** as Edit → Bash → Edit cycles within the same turn. A turn is **one-shot** if `retries == 0`. The Overview's Activities table shows `one_shot_rate` per category — the share of edit turns that succeeded without a retry. A 90% rate means Claude got the edit right on the first try 9 times out of 10.

---

## Optimize detectors

Six waste patterns. Defaults taken from [codeburn](https://github.com/getagentseal/codeburn)'s published thresholds and adapted for Python.

| Detector | Trigger | Fix |
|---|---|---|
| **Duplicate reads across sessions** | Same `Read(file_path=...)` ≥ 5× | `CLAUDE.md` snippet listing files to treat as known |
| **Low Read:Edit ratio** | Ratio < 2 – 3 with ≥ 10 edits in period | `CLAUDE.md` reminder to read before editing |
| **Cache creation overhead** | Avg `cache_creation_input_tokens > 15K` per call | Stabilize `CLAUDE.md` + system prompt content |
| **Junk directory reads** | ≥ 3 reads under `node_modules`, `.git`, `dist`, `build`, `__pycache__`, `.next`, `.venv`, `coverage` | `CLAUDE.md` directive to skip those dirs |
| **Uncapped bash output** | ≥ 20 bash calls with no `BASH_MAX_OUTPUT_LENGTH` env var | `export BASH_MAX_OUTPUT_LENGTH=15000` in your shell profile |
| **Bloated CLAUDE.md** | Top-level `~/.claude/CLAUDE.md` > 200 lines (with `@-import` expansion one level deep) | Move rarely-referenced sections to separate files and `@-import` them |

Scoring — each finding subtracts from a starting score of 100:

| Impact | Penalty |
|---|---|
| high | −15 |
| medium | −7 |
| low | −3 |

Total penalty is capped at 80. Grade bands: A ≥ 90, B ≥ 75, C ≥ 55, D ≥ 30, F otherwise.

---

## CLI

```bash
ccvc                              # interactive browser
```

### CLI command-line options

| Flag | Description |
|---|---|
| `-v`, `--version` | Print current version and exit |
| `--check-update` | Compare installed version to PyPI |
| `--list` | Print conversations non-interactively (pipe-friendly) |
| `--search QUERY` | Filter by keyword (title / project) |
| `--project NAME` | Filter by project name (substring) |
| `--view SESSION_ID` | Print a conversation's full messages |
| `--resume SESSION_ID` | Resume the conversation in Claude Code |
| `--limit N` | Max conversations in `--list` mode (default 50) |

### Interactive commands

| Command | Action |
|---|---|
| `3` | Details for conversation #3 |
| `v 3` | Read full messages of #3 |
| `r 3` | Resume #3 in Claude Code |
| `b 3` | Toggle bookmark on #3 |
| `s flutter` | Search for "flutter" |
| `a` | Clear search, show all |
| `n` / `p` | Next / previous page |
| `h` | Help |
| `q` | Quit |

Session ID prefixes work: `r 4925f6c7`.

### Non-interactive usage

```bash
ccvc --list
ccvc --search "database migration" --list
ccvc --view 4925f6c7
ccvc --list --limit 10
```

### Resuming conversations

```bash
ccvc --resume 4925f6c7
```

Runs `claude --resume <session-id>`. Requires the Claude Code CLI on your `PATH`.

---

## Update notifications

Ledger checks PyPI for a newer version at most once per hour (cached).

- **Web UI** — a banner with current → latest version and an **Update Now** button appears at the top; Settings has a full version panel.
- **CLI** — a one-line notice with the version range appears after the welcome banner.

Manual update / check:

```bash
ccvc --check-update                                         # current vs latest
ccv --update                                                # upgrade via web UI / pipx / pip
pipx upgrade claude-chats-and-analytics-viewer              # direct
```

Fails silently when offline — never blocks startup.

---

## HTTP API

Every panel in the web UI is a thin view over a local JSON API. All endpoints are `GET` unless noted, listen on `127.0.0.1:5005`, and return JSON.

### Core

| Endpoint | Purpose |
|---|---|
| `/` | Single-page app (HTML) |
| `/api/conversations` | All conversation metadata + project list |
| `/api/conversation/<id>` | Full messages for a conversation |
| `/api/export/<id>?format=md\|json` | Download as markdown or JSON |
| `/api/export-all?format=md\|json` | Zip of all conversations |
| `/api/search?q=<query>` | Full-text content search |
| `/api/bookmarks` (GET + POST) | Read or toggle bookmarks |
| `/api/status` | Server heartbeat for auto-refresh |
| `/api/refresh` | Force re-scan of `~/.claude/projects/` |
| `/api/update-check` | `{update_available, current_version, latest_version}` |
| `/api/settings` | Version + package + projects dir + conversation count |
| `/api/do-update` (POST) | Run pipx / uv / pip upgrade in place |

### Dashboard

Every dashboard endpoint accepts the same period parameters: `period=today|7d|30d|month|all|custom` plus `from=YYYY-MM-DD` / `to=YYYY-MM-DD` when `period=custom`.

| Endpoint | Returns |
|---|---|
| `/api/stats` | Legacy aggregate stats (kept for backward compatibility) |
| `/api/dashboard` | Overview: totals, daily, projects, models, activities, core tools, shell, MCP, top sessions, plan |
| `/api/dashboard/optimize` | Findings + A–F grade |
| `/api/dashboard/compare?models=a,b` | Per-model performance + efficiency + behavior |
| `/api/dashboard/yield?project=...` | Git-correlated session outcomes |
| `/api/dashboard/plan` (GET + POST) | Read or set the monthly plan (`claude-max` / `claude-pro` / `cursor-pro` / `custom` / `none`) |
| `/api/dashboard/export?format=csv\|json` | Downloadable multi-period export |

Example:

```bash
curl -s 'http://127.0.0.1:5055/api/dashboard?period=7d' | jq '.overview'
curl -s 'http://127.0.0.1:5055/api/dashboard/optimize?period=30d' | jq '.grade, .findings[0].title'
```

---

## How it works

```
~/.claude/projects/<project-slug>/<session-id>.jsonl
                         │
                         │  (one line per message)
                         ▼
   parser.py  ─────────────────────────────────────┐
     • parse_conversation_metadata  (aggregate)    │
     • parse_conversation_for_dashboard            │
         (per-turn: tools, usage, timestamp,       │
          model, retries, has_edits, category)     │
                                                   │
   classifier.py                                   │
     • 13-category regex + tool-set matcher        │
                                                   │
   pricing.py                                      │
     • model pricing table + turn-level cost       │
                                                   │
   cache.py                                        │
     • version-3 mtime+size keyed metadata cache   │
                                                   ▼
   store.py                   ─── in-memory index ────
                                                   │
                         ┌─────────────────────────┼────────────────────────┐
                         │                         │                        │
                         ▼                         ▼                        ▼
                    web.py (HTTP)           dashboard/aggregator       dashboard/optimize
                                            dashboard/compare          dashboard/yield_tracker
                                            dashboard/export           dashboard/plans
```

Nothing leaves your machine. The LiteLLM pricing service is not contacted; Ledger uses a hardcoded Claude pricing table in `pricing.py`.

---

## Project structure

```
claude-chats-and-analytics-viewer/
├── claude_conversation_viewer/
│   ├── __init__.py           # __version__
│   ├── web.py                # HTTP handler + embedded HTML/CSS/JS
│   ├── cli.py                # Interactive terminal CLI
│   ├── update_checker.py     # PyPI update check
│   ├── pricing.py            # Model pricing + cost estimation
│   ├── classifier.py         # 13-category deterministic classifier
│   ├── parser.py             # JSONL parsing (metadata + per-turn + full messages)
│   ├── cache.py              # Metadata cache v3 + bookmarks + plan storage
│   ├── store.py              # ConversationStore (scans + holds in memory)
│   └── dashboard/            # Dashboard feature subpackage
│       ├── __init__.py
│       ├── aggregator.py     # /api/dashboard payload builder
│       ├── optimize.py       # Waste detectors + A–F grade
│       ├── compare.py        # Per-model performance / efficiency / behavior
│       ├── yield_tracker.py  # git log correlation
│       ├── plans.py          # Subscription preset normalization
│       ├── export.py         # CSV + JSON export
│       └── period.py         # Period / date-range parsing
├── tests/
│   ├── test_version.py
│   ├── test_web.py           # Pricing / parsing / store
│   └── test_dashboard.py     # Classifier, aggregator, optimize grade, plans
├── .github/workflows/
│   ├── publish.yml           # Auto-publish to PyPI on `v*` tag
│   └── ci.yml                # Run tests on push / PR
├── DASHBOARD-PLAN.md         # The design doc for the Dashboard feature
├── pyproject.toml
├── setup.cfg
├── README.md
└── DOCS.md                   # This file
```

---

## Troubleshooting

### "No conversations found"

```bash
ls ~/.claude/projects/
find ~/.claude/projects -name "*.jsonl" | head -5
```

If the directory is empty, you haven't run Claude Code yet.

### Port already in use

Ledger automatically switches to the next available port and prints it. To pin one:

```bash
ccv --port 8080
lsof -i :5005 && kill <PID>        # or kill the existing process
```

### Commands not found after install

After `pipx install` or `pip install --user`, run `pipx ensurepath` (or add the user-site bin dir to your PATH) and open a new terminal.

```bash
export PATH="$(python3 -m site --user-base)/bin:$PATH"    # pip --user path
```

Fallback:

```bash
python3 -m claude_conversation_viewer.web
python3 -m claude_conversation_viewer.cli
```

### Yield tab shows everything as "no-git"

Yield runs `git log` inside each session's `cwd`. If your sessions' `cwd` isn't a git working tree, Yield can't classify outcomes. It's not a bug — the data isn't there.

### Dashboard cache seems stale

Ledger keys the metadata cache by `(mtime, size)` of each JSONL file. If you edit a file externally, the cache invalidates automatically on next load. To force a full re-parse, remove the cache file:

```bash
rm "$TMPDIR/claude-viewer-cache-v3.json"    # macOS / Linux
del %TEMP%\claude-viewer-cache-v3.json      # Windows
```

### Windows notes

- Conversation files are at `%USERPROFILE%\.claude\projects\`
- ANSI colors require Windows 10 1607+ or Windows Terminal
- `--install` is macOS-only; `--install-systemd` is Linux-only. On Windows, use Task Scheduler or the Start-up folder.

### Update check not working

Requires network access to `pypi.org`. Fails silently by design. Use `ccvc --check-update` or `ccv --update` to run it manually.

---

## License

MIT — [github.com/AnshRajput/claude-chats-and-analytics-viewer](https://github.com/AnshRajput/claude-chats-and-analytics-viewer)
