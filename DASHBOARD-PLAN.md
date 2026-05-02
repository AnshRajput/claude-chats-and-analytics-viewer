# Dashboard Plan — codeburn-style observability

Source of truth: [getagentseal/codeburn](https://github.com/getagentseal/codeburn) (README + `src/classifier.ts`, `src/optimize.ts`, `src/providers/*`).

This document has three parts:
1. **Full feature inventory** of codeburn — every widget, command, breakdown, and mechanic.
2. **Plan A — Claude-only dashboard** (our current product): what to build, what changes, what ships in which pass.
3. **Plan B — Multi-platform extension**: how Plan A generalizes to 16 AI coding tools (Codex, Cursor, Gemini, Copilot, etc.).

Everything replaces the current `Stats` tab, which will be renamed **Dashboard**. The single-page HTML app in `claude_conversation_viewer/web.py` (2919 lines) gets a new panel, new API endpoints, and new parser logic. No new external deps required — codeburn uses LiteLLM for pricing; we keep our hardcoded pricing table (already covers all Claude models) and add a refresh-from-LiteLLM path only if we extend to multi-platform.

---

## 1. Full feature inventory of codeburn

### 1.1 Command surface (TUI + flags)

| Command | Purpose |
|---|---|
| `codeburn` | Interactive dashboard (default 7 days) |
| `codeburn today` / `codeburn month` | Preset periods |
| `codeburn report -p <today\|week\|30days\|month\|all>` | Rolling window report |
| `codeburn report --from YYYY-MM-DD --to YYYY-MM-DD` | Exact date range |
| `codeburn report --format json` | Full dashboard as JSON |
| `codeburn report --refresh <seconds>` | Auto-refresh (default 30s, `0` disables) |
| `codeburn status` | One-line compact summary (today + month) |
| `codeburn export -f csv\|json` | Multi-period export |
| `codeburn optimize [-p <period>]` | Waste scan + health grade |
| `codeburn compare [-p <period>]` | Side-by-side model comparison |
| `codeburn yield [-p <period>]` | Productive vs reverted vs abandoned spend (needs git) |
| `codeburn plan set <preset\|custom>` | Subscription tracking |
| `codeburn currency <ISO-4217>` | Display currency |
| `codeburn model-alias <from> <to>` | Fix unpriced proxy model names |
| `codeburn menubar` | macOS menu bar app (out of scope for our web product) |
| `--provider <name>` | Filter any command to one provider |
| `--project <name>` / `--exclude <name>` | Filter by project substring |

Arrow keys in TUI switch periods; `1`-`5` shortcut to Today/7/30/Month/All; `p` cycles providers; `c` opens compare; `o` opens optimize.

### 1.2 Cost-tracking primitives

- Prices every assistant call using **input, output, cache write, cache read, web search** token counts.
- Fast-mode multiplier for Claude.
- Pricing from LiteLLM, cached 24h at `~/.cache/codeburn/`; **hardcoded fallbacks** for all Claude + GPT models.
- Deduplicates by API message id (Claude), cumulative tokens (Codex), conversation+timestamp (Cursor), session id (Gemini), session+message id (OpenCode), responseId (Pi/OMP).

### 1.3 Task classifier (13 categories, deterministic, no LLM)

Implemented in `src/classifier.ts`. Priority rules:

1. **Plan mode** (assistant used `EnterPlanMode`/`ExitPlanMode`) → `planning`
2. **Agent spawn** (assistant used `Task`/agent tool) → `delegation`
3. **Bash-only turn** (no edits) with user message matching:
   - test/pytest/vitest/jest → `testing`
   - git push/commit/merge → `git`
   - npm run build / docker / pm2 / systemctl / brew / cargo build → `build/deploy`
4. Has edit tools (`Edit`/`Write`/`NotebookEdit`) → `coding`, then keyword-refined:
   - debug keywords (fix/bug/error/broken/failing/crash) → `debugging`
   - refactor keywords (refactor/rename/simplify) → `refactoring`
   - feature keywords (add/create/implement/new) → `feature`
5. Bash + reads → `exploration`
6. Bash alone → `coding`
7. WebSearch / MCP / Read-only → `exploration`
8. Skill tool → `general`
9. No tools at all → classify by user message (`brainstorming`, `exploration`, `debugging`, `feature`, `coding`, `conversation`).

### 1.4 One-shot rate (per-category success rate)

For any turn with edits, count **retries** = number of times an `Edit` appears *after* a `Bash` that appeared after an earlier `Edit` in the same turn. A turn is "one-shot" if retries = 0. Column shown per category: `Coding 90%` = 9/10 edit turns succeeded first try.

### 1.5 Dashboard panels (TUI rows, top → bottom)

- **Overview**: cost, API calls, sessions, cache hit %, avg cost per session, 5 most expensive sessions.
- **Plan progress bar** (if plan configured): API-equivalent cost vs monthly price.
- **Daily cost chart**: bar chart by calendar day.
- **Projects**: name, cost, sessions, avg cost per session.
- **Models**: Opus / Sonnet / Haiku / GPT-5 / GPT-4o / Gemini / ..., with token counts.
- **Activities**: 13 categories with cost, calls, and one-shot %.
- **Core tools**: Read, Edit, Bash, Grep, Write, Glob, TodoWrite counts.
- **Shell commands**: most-used bash commands (e.g. `git status`, `ls`, `npm test`).
- **MCP servers**: usage per `mcp__<server>__<tool>` prefix.
- **Period selector**: Today / 7 Days / 30 Days / Month / All Time (or custom `--from`/`--to`).
- **Optimize findings count** in status bar if any; press `o` to open.

### 1.6 Optimize (waste scanner + A–F health grade)

Thresholds and weights from `src/optimize.ts`. Each finding gets: title, explanation, impact (high/medium/low), estimated tokens saved, fix (paste snippet / shell command / file content), and trend (new / improving / resolved vs a 48h recent window).

Detectors:

| Detector | Trigger | Fix |
|---|---|---|
| Files re-read across sessions | Same file read ≥5 times | Add to CLAUDE.md "already read" list or cache |
| Low Read:Edit ratio | Ratio < 2-3 with ≥10 edits | "Read more before editing" guidance |
| Wasted bash output | `BASH_MAX_OUTPUT_LENGTH` uncapped or high (>30k) | Env var snippet setting it to 15k |
| Unused MCP servers | Configured in `~/.claude.json` but 0 calls | `claude mcp remove <name>` |
| Ghost agents | `~/.claude/agents/*.md` never invoked | `mv` to archive |
| Ghost skills | `~/.claude/skills/*` never invoked | `mv` to archive |
| Ghost slash commands | `~/.claude/commands/*` never invoked | `mv` to archive |
| Bloated CLAUDE.md | >400 lines (with `@-import` expansion) | Rewrite suggestion |
| Cache creation overhead | Excessive `cache_creation_input_tokens` | System-prompt stability tip |
| Junk directory reads | Reads under `node_modules`, `.git`, `dist`, `build`, `__pycache__`, `.next`, `.venv`, etc. | Add to `.claudeignore`-equivalent |

**Health grade**: start at 100, subtract 15/7/3 per high/medium/low finding, cap penalty at 80. Grade bands: A ≥ 90, B ≥ 75, C ≥ 55, D ≥ 30, F otherwise. Urgency ranking uses `0.7 × impact_weight + 0.3 × min(tokens_saved / 500_000, 1)`.

### 1.7 Compare (model A vs model B)

Per model in the selected period:

| Section | Metric |
|---|---|
| Performance | One-shot rate |
| Performance | Retry rate (avg retries per edit turn) |
| Performance | Self-correction rate |
| Efficiency | Cost per call |
| Efficiency | Cost per edit |
| Efficiency | Output tokens per call |
| Efficiency | Cache hit rate |
| Behavior | Delegation rate (% turns using Agent tool) |
| Behavior | Planning rate (% turns using plan mode) |
| Behavior | Avg tools per turn |
| Behavior | Fast-mode usage % |
| Behavior | Per-category one-shot rates |

### 1.8 Yield (productive vs reverted vs abandoned)

Correlates session timestamps with `git log` of the repo:

- **Productive** — commits made during the session landed in the main branch.
- **Reverted** — commits exist but a later `git revert` touched them.
- **Abandoned** — no commits within the session window, or commits never merged.

Requires running from inside a git repo.

### 1.9 Plans (subscription tracking)

Presets: `claude-max` ($200/mo), `claude-pro` ($20/mo), `cursor-pro` ($20/mo), `custom --monthly-usd`, `none`. Progress bar shown in overview: `API-equivalent cost / plan price`. Note: presets use stated plan prices, not token allowances (vendors don't publish precise limits).

### 1.10 Currency

Any ISO 4217 code (162 supported). Rates from [Frankfurter](https://www.frankfurter.app/) (ECB, free, no key), cached 24h. Applies everywhere — dashboard, status bar, menu bar, CSV/JSON exports. Config at `~/.config/codeburn/config.json`.

### 1.11 Model aliases

User-mapped model-name overrides for proxy setups that rename models. Built-in aliases ship for common proxy variants. Stored in config; applied before LiteLLM pricing lookup.

### 1.12 Filtering

- `--provider <claude|codex|cursor|...>` — any command, one provider only.
- `--project <name>` / `--exclude <name>` — case-insensitive substring, combinable.
- `--from` / `--to` — either bound alone is valid; inverted/malformed dates error clearly.

### 1.13 JSON output

`report`, `today`, `month`, `status` all support `--format json`: full payload including overview, daily, projects (with `avgCostPerSession`), models with token counts, activities with one-shot rates, core tools, MCP servers, shell commands.

### 1.14 Menu bar (macOS only)

Native Swift + SwiftUI app in `mac/`. Shows today's spend, period switcher, Trend, Forecast, Pulse, Stats, Plan insights, optimize findings, export. Out of scope for our Python/HTTP product — we already have a web UI that serves the same role.

### 1.15 Providers (16)

Claude Code, Claude Desktop, Codex (OpenAI), Cursor, cursor-agent, Gemini CLI, GitHub Copilot, Kiro, OpenCode, OpenClaw, Pi, OMP, Droid, Roo Code, KiloCode, Qwen. Each provider is one file in `src/providers/`. Cursor + OpenCode use SQLite (`better-sqlite3` optional dep); the rest read JSONL or JSON from disk. Dedup strategy and token-count shape varies per provider (documented above in 1.2).

---

## 2. Plan A — Claude-only dashboard (v1, ship first)

### 2.1 Scope philosophy

Codeburn has roughly 40 surfaces. Blindly porting all of them would explode `web.py` (already 2919 lines) and add hundreds of kB to the single-file HTML bundle. Plan A ships the 80/20 subset that materially improves our current Stats tab:

- Everything the classifier, optimize, compare, and yield features need lives in data we **already have on disk** (`~/.claude/projects/*.jsonl`).
- We stay dependency-free (stdlib only) except where absolutely necessary.
- We use native `<canvas>` / CSS for charts — no Chart.js / D3. (Keeps the single-file bundle promise intact.)

### 2.2 What ships in v1 (ordered by priority)

**Tier 1 — ship in the first PR:**

1. **Rename** `Stats` → `Dashboard` (tab, handler, CSS, JS).
2. **Period selector**: Today / 7 Days / 30 Days / This Month / All Time + custom date range. Currently the tab shows all-time only.
3. **Overview KPI cards** extended with:
   - API calls
   - Sessions (= conversations — already there)
   - **Cache hit rate** % (new; = cache_read / (input + cache_read))
   - **Avg cost per session**
   - **Today's cost** + **This month's cost** next to totals
4. **Daily cost chart** — we have a conversations-per-day heatmap; add a bar chart showing $ per day.
5. **13-category task classifier** + **Activities panel** with cost, calls, one-shot %. Port `classifier.ts` to Python 1:1 (deterministic regex — no LLM). This is the biggest new capability — everything else builds on it.
6. **Core tools panel** — counts per `Read`, `Edit`, `Write`, `Bash`, `Grep`, `Glob`, `TodoWrite`, `Task`, `WebSearch`, `WebFetch`.
7. **Shell commands panel** — top 20 most-used bash commands (first token of `tool_use.input.command`).
8. **MCP servers panel** — counts per `mcp__<server>__<tool>` prefix.
9. **5 most expensive sessions** (table; click to open).
10. **JSON API**: `/api/dashboard?period=<today|7d|30d|month|all|custom>&from=&to=` returning the full payload.

**Tier 2 — second PR (Optimize + Compare):**

11. **Optimize scan** (`/api/dashboard/optimize`) with 6 of the 10 detectors — the ones that don't require scanning `~/.claude/agents/`, `~/.claude/skills/`, `~/.claude/commands/`, and `CLAUDE.md`:
    - Files re-read across sessions
    - Low Read:Edit ratio
    - Cache creation overhead (system prompt instability)
    - Junk directory reads
    - Wasted bash output detection (looks at tool_result size)
    - Bloated CLAUDE.md (walks `~/.claude/CLAUDE.md` + project-level CLAUDE.md files with `@-import` expansion)
    - **A–F health grade** with the same 100/15/7/3/80 weighting.
12. **Compare panel** — model A vs model B table with the 11 metrics from 1.7.
13. **One-shot rate** on per-category rows and per-model rows.

**Tier 3 — third PR (Yield + Plans + Polish):**

14. **Yield** — correlate sessions with `git log` in `cwd` (if it's a git repo). Productive / Reverted / Abandoned columns per project.
15. **Plans** — `claude-pro` / `claude-max` / custom. Store in `~/.claude/viewer-plan.json` (next to `viewer-bookmarks.json`). Progress bar.
16. **Export** — add CSV/JSON download button.
17. **Currency** — optional; defer unless requested. We already show USD.

**Tier 4 — skipped intentionally** (not worth the complexity for our product):

- Menu bar app (our web UI already solves the same problem).
- Model aliases (only needed when users run Claude through a renaming proxy).
- Ghost agents/skills/commands detection (high value but scans outside the data directory — separate security story).
- Trend / Forecast / Pulse (codeburn's menu-bar-only panels).

### 2.3 Concrete file-level changes (Tier 1)

All in `claude_conversation_viewer/web.py` unless noted.

**Parser changes** (`parse_conversation_metadata` + new `parse_conversation_for_dashboard`):
- Per-turn (not just aggregate) data: for each assistant turn capture `{ timestamp, model, tools: [name, ...], tool_inputs: [{...}], usage: {...} }`.
- Capture the pairing user message for each assistant turn (needed by classifier).
- Extend metadata cache (bump `CACHE_VERSION` to 3) to store per-turn data. This is the biggest correctness risk — must be gated behind cache version bump so we don't poison the existing cache.

**New module-level constants** (copied from `classifier.ts`):
- `TEST_PATTERNS`, `GIT_PATTERNS`, `BUILD_PATTERNS`, `INSTALL_PATTERNS`
- `DEBUG_KEYWORDS`, `FEATURE_KEYWORDS`, `REFACTOR_KEYWORDS`, `BRAINSTORM_KEYWORDS`, `RESEARCH_KEYWORDS`
- `EDIT_TOOLS`, `READ_TOOLS`, `BASH_TOOLS`, `TASK_TOOLS`, `SEARCH_TOOLS`

**New functions**:
- `classify_turn(turn) -> category, retries, has_edits` (port of `classifier.ts`)
- `aggregate_dashboard(conversations, period) -> dict` (overview, daily, projects, models, activities, tools, shell, mcp, top_sessions)
- `compute_one_shot_rate(turns) -> dict[category, float]`
- `parse_period(s) -> (start, end)` handling `today | 7d | 30d | month | all | custom`

**New API endpoints**:
- `GET /api/dashboard?period=7d&from=&to=` — replaces `/api/stats` (old endpoint stays for backward compat, redirects/aliases to new one)
- `GET /api/dashboard/optimize?period=7d` (Tier 2)
- `GET /api/dashboard/compare?period=7d&models=a,b` (Tier 2)
- `GET /api/dashboard/yield?period=7d&project=...` (Tier 3)

**HTML/CSS/JS changes**:
- Tab: `stats` → `dashboard` (`onclick`, `data-tab`, label)
- New panel sections: Period tabs, Activities bar chart, Core tools bar chart, Shell bar chart, MCP bar chart, Top expensive sessions table, Daily cost line chart
- Canvas-based daily cost line chart (no Chart.js — keep single-file bundle)
- Existing heatmap stays (it's codeburn-adjacent but more GitHub-like)

### 2.4 Performance budget

- Plan A adds ~20ms per conversation parse for per-turn data extraction. Parsing ~500 sessions stays under 2s with the existing cache (mtime + size keyed).
- Dashboard endpoint aggregates in memory from the already-loaded store — <100ms for ≤5k conversations.
- Optimize detector walks per-turn tool calls; still O(n) over turns.

### 2.5 Testing

- `tests/test_web.py` already exercises the HTTP handler. Add cases for:
  - `/api/dashboard?period=today` returns ≤1 day of data
  - Classifier fixtures: build a small JSONL with known tools and assert category
  - `/api/dashboard/optimize` returns findings + grade
- Snapshot a real JSONL from `~/.claude/projects/` into `tests/fixtures/` for deterministic runs.

### 2.6 Risk register

| Risk | Mitigation |
|---|---|
| Cache poisoning from v2 → v3 schema | Bump `CACHE_VERSION = 3`; old cache discarded gracefully |
| Per-turn data explodes cache size | Store per-turn as a compact array-of-tuples; current cache is ~50kB per 1k sessions, new is ~200kB |
| Classifier mis-categorizing edge cases | Ship behind a debug query param `?debug=1` that returns reason string for each turn |
| JSONL schema drift across Claude Code versions | Use `.get()` everywhere; existing parser already tolerant |
| Plan A makes the single-file HTML bundle cross 100kB | Compress HTML response with gzip; lazy-load dashboard JS only when tab opens |

### 2.7 Deliverable surface

Before: `Stats` tab shows KPI grid + heatmap + model-usage bars + project-count bars.
After: `Dashboard` tab with period selector, overview KPIs, daily cost chart, daily heatmap, activities, top 5 expensive, models, projects, core tools, shell, MCP.

---

## 3. Plan B — Multi-platform extension

Plan B turns Plan A into a 16-provider observability tool (matching codeburn's coverage). It depends on Plan A being shipped first.

### 3.1 Architecture split

Right now `web.py` hardcodes `~/.claude/projects/`. Plan B introduces a provider registry:

```
claude_conversation_viewer/
├── providers/
│   ├── __init__.py         # registry + discovery
│   ├── base.py             # Provider ABC: name, data_dirs(), discover_sessions(), parse_session()
│   ├── claude_code.py      # current behavior, extracted
│   ├── claude_desktop.py   # ~/Library/Application Support/Claude/local-agent-mode-sessions/
│   ├── codex.py            # ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
│   ├── cursor.py           # SQLite (better-sqlite3 equivalent — sqlite3 stdlib works)
│   ├── cursor_agent.py     # ~/.cursor/projects/
│   ├── gemini.py           # ~/.gemini/tmp/<project>/chats/
│   ├── copilot.py          # ~/.copilot/session-state/ + VS Code workspaceStorage/
│   ├── kiro.py             # .chat JSON files
│   ├── opencode.py         # SQLite ~/.local/share/opencode/
│   ├── openclaw.py         # ~/.openclaw/agents/ (+ legacy .clawdbot etc.)
│   ├── pi.py               # ~/.pi/agent/sessions/
│   ├── omp.py              # ~/.omp/agent/sessions/
│   ├── droid.py            # ~/.factory/projects/
│   ├── roo_code.py         # VS Code globalStorage/rooveterinaryinc.roo-cline/tasks/
│   ├── kilo_code.py        # VS Code globalStorage/kilocode.kilo-code/tasks/
│   └── qwen.py             # ~/.qwen/projects/<project>/chats/
├── pricing.py              # LiteLLM fetch + 24h cache at ~/.cache/claude-viewer/
└── web.py                  # handler + store + HTML; imports providers
```

Provider interface (`base.py`):

```python
class Provider(Protocol):
    name: str
    label: str
    data_dirs: list[Path]

    def discover_sessions(self) -> Iterable[Path]: ...
    def parse_session(self, path: Path) -> list[Turn]: ...  # unified Turn schema
    def dedup_key(self, turn: Turn) -> str: ...
```

Platform-independent path resolution:
- Linux uses `$XDG_DATA_HOME`, `~/.config`, etc.
- Windows uses `%APPDATA%`, `%LOCALAPPDATA%`.
- macOS uses `~/Library/Application Support/`.
- Env overrides: `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `FACTORY_DIR`, `QWEN_DATA_DIR`.

### 3.2 Pricing module

Port codeburn's LiteLLM flow:
1. Fetch `https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json` → cache at `~/.cache/claude-viewer/pricing.json` (24h TTL).
2. Keep our hardcoded table as the fallback.
3. Apply user-configured model aliases (Tier 2 for multi-platform) from `~/.config/claude-viewer/config.json`.

### 3.3 UI changes

- Add a **provider switcher** in the dashboard header (auto-detected from disk): `Claude | Codex | Cursor | ...`.
- `All` mode merges across providers with a color-coded legend.
- Per-provider caveats rendered as info tooltips (e.g. "Cursor Auto mode uses estimated Sonnet pricing").
- Cursor and OpenCode trigger a one-time "first load may take up to a minute" banner.

### 3.4 SQLite providers (Cursor, OpenCode)

Python has `sqlite3` in stdlib — no `better-sqlite3` equivalent needed. Risks:
- Cursor database is live and locked when Cursor is running; open with `mode=ro&immutable=1`:
  ```python
  sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
  ```
- Token counts for Cursor come from `cursorDiskKV` table rows with keys prefixed `bubbleId:`; values are JSON.
- OpenCode excludes subtask sessions (`parent_id IS NOT NULL`).
- Cache parsed results at `~/.cache/claude-viewer/cursor-results.json`, invalidate on DB mtime.

### 3.5 Dedup

Each provider owns its dedup key (§1.2). Unified `Turn` schema carries `provider_id` + `dedup_key`; the store uses a dict keyed by `(provider, dedup_key)`.

### 3.6 Classifier portability

The 13-category classifier in §1.3 is mostly provider-agnostic (regex on user messages + tool name matching). Tool names differ across providers (Cursor uses `cursor:edit`, Codex has its own names). Extend `EDIT_TOOLS` / `READ_TOOLS` / `BASH_TOOLS` sets to include the aliases from each provider file. Codeburn does exactly this in its classifier.

### 3.7 Cursor-specific UI

Codeburn shows a **Languages** panel instead of Core Tools/Shell/MCP when Cursor is selected (Cursor doesn't log individual tool calls). Replicate.

### 3.8 Gemini/Kiro/Copilot quirks

- Gemini reports input tokens inclusive of cached → subtract cached from input before pricing.
- Kiro has no model field → label `kiro-auto`, price at Sonnet rate.
- Copilot has no explicit token counts → estimate from content length; infer model from tool-call-id prefix.

### 3.9 Rollout plan

1. **Ship Plan A in full** first.
2. **Refactor** `web.py` to extract `providers/claude_code.py` (no new behavior). Introduce `Provider` ABC. This is a safe, internal-only change.
3. Add providers one at a time, simplest first: **Codex → Gemini → Cursor → Copilot → rest**. Each new provider = one file + one registry entry + smoke test.
4. Add **pricing.py** with LiteLLM integration behind a feature flag.
5. Add **provider switcher** UI.
6. Add **multi-platform optimize** (most detectors are Claude-specific — CLAUDE.md, MCP servers; generalize where possible, skip where not).

### 3.10 What stays Claude-only in Plan B

Some codeburn optimize detectors only make sense for Claude Code:
- Ghost agents/skills/commands — these live in `~/.claude/`
- CLAUDE.md bloat — Claude Code only
- MCP server auditing — Claude Code only

The other provider dashboards show Overview + Daily + Projects + Models + Activities + 5 expensive. Optimize is scoped per-provider.

---

## 4. What we ship

- **Now (this PR)**: Plan A Tier 1 — Dashboard tab, period selector, classifier, activities panel, core tools, shell, MCP, top expensive sessions, daily cost chart, JSON API.
- **Next**: Plan A Tier 2 — Optimize + Compare.
- **Then**: Plan A Tier 3 — Yield + Plans + Export.
- **Later**: Plan B refactor and additional providers.
