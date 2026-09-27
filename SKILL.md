---
name: trainingpeaks
description: Read, edit, create, and delete TrainingPeaks training plans. Pull fitness (CTL/ATL/TSB), health metrics (HRV/RHR/sleep/weight), workouts, and personal records. Mutate planned workouts via CLI — edit metadata, replace interval structure (YAML DSL or raw JSON), move dates, delete, or create from scratch. Uses cookie-based authentication (no API key needed). Use with endurance, cycling, running, or swimming triathlon coach skills for best results.
---

# TrainingPeaks Skill

CLI access to the TrainingPeaks internal API. Pure Python stdlib plus one dependency (**PyYAML**) for the workout-structure DSL.

## Setup: Getting Your Auth Cookie

1. Log in to [TrainingPeaks](https://app.trainingpeaks.com) in your browser.
2. Open DevTools → Application → Cookies → `app.trainingpeaks.com`.
3. Find the cookie named `Production_tpAuth`.
4. Copy its value (long encoded string).

Then authenticate:

```bash
python3 scripts/tp.py auth "<paste_cookie_value_here>"
```

Or set the environment variable (useful for CI/scripts):

```bash
export TP_AUTH_COOKIE="<cookie_value>"
```

Credentials stored in `~/.trainingpeaks/` with `0600` permissions. Cookies last weeks; Bearer tokens auto-refresh from the stored cookie every ~1h.

## Agent Availability

Agents running as the same operating-system user can access the shared `~/.trainingpeaks/` authentication and token storage. Only grant access to trusted agents.

Keep this reusable skill account-neutral: resolve athlete IDs from authentication, use placeholders in examples, and keep personal settings, coaching preferences, and real API responses outside the repository. Run examples from the skill directory. Set `WORKOUT_ID` and `COMMENT_ID` to IDs from your own account before using examples that reference them; sample dates and durations are illustrative.

---

## Commands

Commands are grouped by purpose:

- **Auth** — `auth`, `auth-status`
- **Read** — `profile`, `workouts`, `workout`, `fitness`, `health`, `peaks`, `week-summary`, `events-atp`
- **Mutate** — `create`, `edit-meta`, `edit-structure`, `edit-date`, `delete`, `edit`
- **Comments** — `add-comment`, `delete-comment`
- **Files** — `download-fit`, `sync-fit`

---

## Auth

### `auth <cookie>`

Store and validate a `Production_tpAuth` cookie. Exchanges it for a Bearer token and caches the athlete ID.

```bash
python3 scripts/tp.py auth "V001..."
# ✓ Authenticated successfully!
#   Athlete ID: 12345
#   Token expires in: 60 minutes
```

### `auth-status`

```bash
python3 scripts/tp.py auth-status
# Cookie: stored (file)
# Token: valid (42m remaining)
# Athlete ID: 12345
# ✓ Ready
```

---

## Read

### `events-atp` — Athlete Events / ATP Calendar

Pull athlete events (races, ATP priority, race type) via the fitness v6 API. Not a CLI command yet — access via `api_get()`:

```python
import sys
sys.path.insert(0, "scripts")
from tp import api_get, get_athlete_id

athlete_id = get_athlete_id()
status, data = api_get(f"/fitness/v6/athletes/{athlete_id}/events/2026-01-01/2026-12-31")
if status != 200 or not isinstance(data, list):
    raise RuntimeError(f"Events request failed: HTTP {status}")
for ev in sorted(data, key=lambda x: x["eventDate"]):
    print(f"{ev['eventDate'][:10]}  {ev['atpPriority']:<4}  {ev['eventType']:<20} {ev['name']}")
```

Returns event date, name, type (`CyclingMountain`, `CyclingRoad`), race duration category (`MtbOverTwoHours`, `MtbUnderTwoHours`), and **ATP priority** (A/B/C). Use when the athlete asks about upcoming races, nationals, or scheduling — events carry priority and race-type info that workouts don't.

See `references/events-atp-endpoint.md` for full field reference and discovery notes.

### `profile [--json]`

```bash
python3 scripts/tp.py profile
# Shows the authenticated athlete's name, ID, and current thresholds.
```

### `workouts <start> <end> [--filter all|planned|completed] [--json]`

List workouts in a date range (max 90 days).

```bash
python3 scripts/tp.py workouts 2026-04-21 2026-05-02
python3 scripts/tp.py workouts 2026-04-01 2026-04-30 --filter completed
python3 scripts/tp.py workouts 2026-04-21 2026-04-21 --json
```

Output columns: Date · Title · Sport · Status (✓/○) · Planned duration · Actual duration · TSS · Distance.

### `workout <id> [--json]`

Full detail for a single workout including description, coach comments, structure, and all metrics.

```bash
python3 scripts/tp.py workout "$WORKOUT_ID"
```

### `fitness [--days 90] [--json]`

CTL (fitness), ATL (fatigue), TSB (form) time series. Max 365 days.

```bash
python3 scripts/tp.py fitness
python3 scripts/tp.py fitness --days 365 --json
```

### `health <start> <end> [--json]`

Consolidated health metrics: HRV, resting heart rate, weight, sleep duration + quality.

```bash
python3 scripts/tp.py health 2026-04-14 2026-04-21
python3 scripts/tp.py health 2026-04-01 2026-04-30 --json
```

### `peaks <sport> <pr_type> [--days 3650] [--json]`

Personal records ranked by sport and metric.

```bash
python3 scripts/tp.py peaks Bike power20min
python3 scripts/tp.py peaks Bike power60min --days 365
python3 scripts/tp.py peaks Run speed5K
```

**Valid PR types:**

| Sport | Types |
|-------|-------|
| Bike  | `power5sec`, `power1min`, `power5min`, `power10min`, `power20min`, `power60min`, `power90min`, `hR5sec`, `hR1min`, `hR5min`, `hR10min`, `hR20min`, `hR60min`, `hR90min` |
| Run   | `hR5sec`–`hR90min`, `speed400Meter`, `speed800Meter`, `speed1K`, `speed1Mi`, `speed5K`, `speed5Mi`, `speed10K`, `speed10Mi`, `speedHalfMarathon`, `speedMarathon`, `speed50K` |

### `week-summary [--json]`

Current week's completed + planned TSS alongside current CTL/ATL/TSB.

```bash
python3 scripts/tp.py week-summary
# Shows the current week, CTL/ATL/TSB, and completed/planned TSS.
```

---

## Mutate — Edit / Create / Delete Planned Workouts

All mutation commands follow the same safety contract:

- **Refuse to edit completed workouts** (`tssActual != null`) unless `--force` is passed.
- **`--dry-run`** prints a per-field diff and returns without calling the API.
- After every successful PUT, the workout is **re-fetched and verified** — you see `✓ verified: <fields>`.

### `create [flags]`

Create a new planned workout via POST.

```bash
python3 scripts/tp.py create \
  --date 2026-01-15 \
  --title "Example interval session" \
  --sport bike \
  --from-yaml intervals.yaml \
  --auto-tss
# Reports modelled IF/TSS and the newly created workout ID.
```

**Flags:**

| Flag | Notes |
|---|---|
| `--date YYYY-MM-DD` | **Required.** |
| `--title STR` | **Required.** Empty strings rejected. |
| `--sport {bike,run,swim,strength,walk,other}` | Default: `bike` |
| `--type-id INT` | Raw `workoutTypeValueId` — overrides `--sport`. For sports not in the named set, obtain the ID from an existing workout of the intended type and verify the displayed sport in TP. |
| `--from-yaml FILE` | DSL (see below). |
| `--from-file FILE` | Raw TP structure JSON (mutex with `--from-yaml`). |
| `--duration FLOAT` | Hours. Required only if no structure is provided. Ignored (with warning) when structure is provided — derived from it instead. |
| `--tss FLOAT`, `--if FLOAT` | Optional. Explicit values override `--auto-tss`; otherwise values remain unset unless estimated from YAML. |
| `--auto-tss` | With `--from-yaml`, derives TSS/IF from the structure. Ignored (with warning) for `--from-file` or no structure. |
| `--description STR` | Optional. |
| `--dry-run` | Print POST payload, don't send. |

**CLI sport defaults:** `bike=2, run=3, swim=1, strength=9, walk=7, other=8`. These are the CLI's built-in mappings, not a guarantee of the label shown by every account/API version. Check `workoutTypeValueId` on an existing workout of the intended sport before using `--type-id`, especially for MTB, Walk, and Other.

Creation leaves `energyPlanned` unset rather than estimating it from a fixed athlete FTP. Set an explicit value later with `edit-meta --energy` if needed.

### `edit-meta <id> [flags]`

Edit metadata fields on a planned workout.

```bash
python3 scripts/tp.py edit-meta "$WORKOUT_ID" --title "Recovery Ride — reduced" --tss 15 --duration 0.58
python3 scripts/tp.py edit-meta "$WORKOUT_ID" --if 0.55 --dry-run
```

Flags: `--title`, `--description`, `--tss`, `--if`, `--duration` (hours), `--energy` (kJ), `--dry-run`, `--force`.

### `edit-structure <id> [flags]`

Replace the interval structure of a planned workout. Exactly one of `--from-yaml` or `--from-file` is required.

```bash
python3 scripts/tp.py edit-structure "$WORKOUT_ID" --from-yaml intervals.yaml --auto-tss --dry-run
python3 scripts/tp.py edit-structure "$WORKOUT_ID" --from-yaml intervals.yaml --auto-tss
# Reports modelled IF/TSS for the supplied YAML.
# ✓ verified: structure, totalTimePlanned, ifPlanned, tssPlanned
```

Flags: `--from-yaml FILE` | `--from-file FILE` (mutex, one required) · `--auto-tss` (with `--from-yaml`: also sets `tssPlanned`/`ifPlanned` from the estimator) · `--dry-run` · `--force`.

Derives `totalTimePlanned` from the structure's cumulative duration automatically.

### `edit-date <id> --to YYYY-MM-DD [flags]`

Move a planned workout to a different date. Preserves time-of-day in `startTimePlanned` if present.

```bash
python3 scripts/tp.py edit-date "$WORKOUT_ID" --to 2026-04-22 --dry-run
python3 scripts/tp.py edit-date "$WORKOUT_ID" --to 2026-04-22
# ✓ verified: workoutDay
```

### `delete <id> [--yes] [--force]`

Delete a planned workout. Interactive `[y/N]` prompt unless `--yes` is passed. Refuses to delete completed workouts without `--force`.

```bash
python3 scripts/tp.py delete "$WORKOUT_ID"           # prompts
python3 scripts/tp.py delete "$WORKOUT_ID" --yes     # skip prompt
# ✓ deleted: 'Recovery Ride' on 2026-04-24
```

Exit 130 on user abort.

### `edit <id> --from-file FILE [flags]`

**Escape hatch** — full-PUT replacement from a raw TP workout JSON file (e.g., a snapshot you exported with `workout <id> --json`). Discards the current state entirely; the file contents become the new workout.

```bash
python3 scripts/tp.py workout "$WORKOUT_ID" --json > backup.json
# (edit backup.json by hand)
python3 scripts/tp.py edit "$WORKOUT_ID" --from-file backup.json
```

Flags: `--from-file FILE` (required) · `--dry-run` · `--force`.

---

## DSL Format — `--from-yaml`

`edit-structure` and `create` accept a **YAML workout structure** via `--from-yaml`. The DSL translates into TP's nested `structure` dict automatically.

### Minimum example — flat workout

```yaml
steps:
  - { type: warmup,   duration: 10min, ftp: 40-50 }
  - { type: active,   duration: 20min, ftp: 60-70, name: "Easy spin" }
  - { type: cooldown, duration: 5min,  ftp: 40-50 }
```

### With repetitions

```yaml
steps:
  - { type: warmup,  duration: 10min, ftp: 40-50 }
  - { type: active,  duration: 5min,  ftp: 70-80, name: "Tempo bridge" }
  - repeat: 3
    steps:
      - { type: active, duration: 4min, ftp: 105-115, name: "VO2" }
      - { type: rest,   duration: 5min, ftp: 50-60,   name: "Rest" }
  - { type: cooldown, duration: 10min, ftp: 40-50 }
```

### Schema

| Field | Required | Values |
|---|---|---|
| `type` | yes (in each step) | `warmup` \| `active` \| `rest` \| `cooldown` |
| `duration` | yes (in each step) | `<N>s`, `<N>min`, `<N>h`, or compound `1h30min` |
| `ftp` | yes (in each step) | `N` (single target) or `N-M` (range); integer percent of FTP, 1–200 |
| `name` | no | Defaults per `type`: "Warm up" / "Active" / "Recovery" / "Cool down" |
| `repeat` | only on rep blocks | Integer ≥ 2 |

### Limits

- Intensity metric: **`ftp` (percentOfFtp) only** — no `watts:` or `hr:` support yet.
- Rep blocks are **flat** — no rep-inside-rep nesting.
- Out-of-bounds or malformed input fails before any HTTP call, pointing to the offending step index.

### What gets emitted

The translator produces TP's native shape:

```json
{
  "primaryLengthMetric": "duration",
  "primaryIntensityMetric": "percentOfFtp",
  "primaryIntensityTargetOrRange": "range",
  "structure": [...],
  "polyline": [[x, y], ...]
}
```

The `polyline` is auto-computed — a step-wise silhouette for TP's chart rendering.

### TSS/IF estimation

`--auto-tss` derives planned TSS and IF from a **modelled normalized-power** trace:

- each FTP-targeted step is represented at the midpoint of its range and expanded to a one-second power trace;
- **modelled NP** uses standard 30-second rolling averages with fourth-power weighting;
- **IF** = modelled NP ÷ FTP (equivalently, modelled NP as a fraction of FTP);
- **TSS** = `duration_hours × IF² × 100`.

This remains a planned model: it cannot know trainer ramps, coasting, cadence, or actual execution. It is materially more appropriate for variable intervals than a simple target-power average.

Use `--auto-tss` when a modelled planned-load estimate is wanted. Omit it and explicit `--tss` / `--if` to leave planned load unset on creation. Explicit flags always win. Planned estimates are not actual post-workout TSS; choose the workflow with the athlete or coach rather than embedding individual preferences in this skill. See `references/planned-tss-ownership.md`.

---

## Comments

### `add-comment <workout_id> <comment>`

```bash
python3 scripts/tp.py add-comment "$WORKOUT_ID" "Felt strong in final intervals"
# ✓ Comment added (id <comment_id>)
```

### `delete-comment <workout_id> <comment_id>`

Comment IDs come from `workout <id>` output.

```bash
python3 scripts/tp.py delete-comment "$WORKOUT_ID" "$COMMENT_ID"
# ✓ Comment <comment_id> deleted.
```

---

## Files

### `download-fit <workout_id> [flags]`

Download the raw FIT file for a completed workout.

```bash
python3 scripts/tp.py download-fit "$WORKOUT_ID" --output-dir ./fit_exports
# ✓ Saved FIT file to ./fit_exports/<workout_id>-<file_id>.fit.gz
```

Flags: `--file-id ID` (optional; auto-discovered from workout details otherwise), `--output-dir DIR`, `--output-name NAME`.

### `sync-fit [flags]`

Sync recent completed FIT files locally. Intended for cron / periodic triggers.

```bash
python3 scripts/tp.py sync-fit --days 7 --sport Bike --output-dir ./fit_exports --once
```

Flags: `--days N` (default 7), `--sport Bike|Run|All` (default Bike), `--output-dir DIR`, `--once` (ignore periodic scheduling state), `--min-interval-min N`, `--max-interval-min N`.

The optional `scripts/run_tp_sync_fit_cron.py`, `scripts/run_sync_fit_cron.py`, and `scripts/run_tp_sync_fit.sh` wrappers locate `tp.py` next to themselves. They default to three days and `fit_exports` relative to the caller's working directory. Additional arguments override those defaults; configure an absolute `--output-dir` in cron. They do not assume an agent workspace or change directories.

Personal nutrition/note-sync automation is outside this skill; no nutrition targets or vault writes are bundled.

---

## Updating Feeling / RPE on Completed Workouts

The `edit-meta` and `edit` commands only handle **planned** fields (`tssPlanned`, `ifPlanned`, `totalTimePlanned`, `title`, etc.). They do **not** support updating `feeling` or `rpe` on completed workouts — the `_diff_fields` check only compares planned verify fields, so changes to feeling/RPE silently produce "No changes to apply."

**To update feeling/RPE, use a direct API PUT from Python:**

```python
import sys, json, os
sys.path.insert(0, "scripts")
from tp import api_get, api_put, _workout_url
from datetime import datetime

# Set these environment variables using your own workout and requested ratings.
# Verify the current TP UI/API rating scale; do not assume numeric labels.
workout_id = int(os.environ["WORKOUT_ID"])
status, data = api_get(_workout_url(workout_id))
if status != 200 or not data:
    raise RuntimeError(f"Workout request failed: HTTP {status}")
w = data[0] if isinstance(data, list) else data

w['feeling'] = int(os.environ["FEELING_VALUE"])
w['rpe'] = int(os.environ["RPE_VALUE"])

if isinstance(w.get("structure"), dict):
    w["structure"] = json.dumps(w["structure"])
w["lastModifiedDate"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

status, result = api_put(_workout_url(workout_id), w)
if not 200 <= status < 300:
    raise RuntimeError(f"Workout update failed: HTTP {status}")
status, data = api_get(_workout_url(workout_id))
if status != 200 or not data:
    raise RuntimeError(f"Workout verification failed: HTTP {status}")
verified = data[0] if isinstance(data, list) else data
if any(verified.get(key) != w[key] for key in ("feeling", "rpe")):
    raise RuntimeError("Workout ratings did not match the requested values")
```

This pattern works for any field the CLI mutation commands don't cover (feeling, rpe, userTags, etc.).

---

## Token Management

- Bearer tokens cached in `~/.trainingpeaks/token.json` (expire ~1h; auto-refreshed from stored cookie).
- `Production_tpAuth` cookie persists for weeks — stored in `~/.trainingpeaks/cookie`.
- If the cookie expires, you get a clear `Not authenticated. Run: tp.py auth <cookie>` error.

### Auth Failure Troubleshooting

When `tp.py auth` or any API call fails, distinguish the failure type:

| HTTP Status | Meaning | Action |
|---|---|---|
| **401** | Cookie expired or invalid | Ask user for a fresh `Production_tpAuth` cookie |
| **500** | **TP server-side outage** — not a cookie problem | Store cookie manually + set up auto-retry (see below) |
| **403/404** | Endpoint moved or blocked | Check API base URL / endpoint path in `tp.py` |

**HTTP 500 from the token endpoint** (`tpapi.trainingpeaks.com/users/v3/token`) is a TrainingPeaks server-side outage. The response body is `{"message":"An error has occurred."}`. This is NOT a cookie format issue — valid cookies with 500 = server down; invalid cookies get 401.

**Manual cookie storage fallback** (when `tp.py auth` fails with 500 but the cookie itself is valid):

```python
import os, stat
cookie = "<paste_cookie_value>"
path = os.path.expanduser("~/.trainingpeaks/cookie")
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w") as f:
    f.write(cookie.strip())
os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
```

Once stored, the next successful token exchange will auto-refresh — no need to re-run `tp.py auth`. Any `tp.py` command that calls `ensure_token()` will pick up the stored cookie automatically.

**Auto-retry for server outages**: Create a cron job (every 15 min, ~12 repeats) that runs `tp.py auth-status` + a lightweight read command. If it succeeds, report recovery; if still 500, stay silent (HEARTBEAT_OK). See `references/auth-troubleshooting.md` for the full debugging recipe.

## File Locations

| File | Purpose |
|---|---|
| `~/.trainingpeaks/cookie` | Stored `Production_tpAuth` cookie |
| `~/.trainingpeaks/token.json` | Cached OAuth Bearer token + expiry |
| `~/.trainingpeaks/config.json` | Cached athlete ID and account info |

## Exit Codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Generic error |
| 2 | Validation / precondition failure (bad input, missing required flag, refused mutation) |
| 3 | Auth / network failure |
| 4 | Post-mutation verify failed (data landed differently than sent) |
| 130 | User aborted interactive prompt |

## Notes

- All dates use `YYYY-MM-DD`. Mutations accept time-of-day preservation where applicable.
- `workouts` query range is capped at 90 days; split larger spans into chunks.
- Rate limit: 150ms minimum between API requests (handled internally).
- `TP_AUTH_COOKIE` environment variable overrides the stored cookie.
- Default output is human-readable; `--json` returns raw API responses.
- **PyYAML** is required for the DSL (`edit-structure --from-yaml` / `create --from-yaml`). Install with `pip install pyyaml`.
- **Cookies expire mid-session.** `auth-status` checks the stored cookie and cached token, but a token can expire between calls during a long session. If any command returns `Not authenticated. Run: tp.py auth <cookie>`, the cookie has expired — ask the user for a fresh `Production_tpAuth` cookie. Data fetched earlier in the same session is still valid; just re-authenticate and continue.
- **`execute_code` terminal() returns a dict, not a string.** When calling `tp.py` from `execute_code`, the return value of `terminal()` is a dict like `{"output": "...", "exit_code": 0}`. Access `result["output"]` to get the stdout string, then `json.loads()` on that — do not `json.loads(result)` directly.
- **`.env` files are protected — use `hermes config set`.** Profile `.env` files cannot be written via `write_file` or `terminal` (blocked by the protected-file guard). To set environment variables like `WIKI_PATH` or `OBSIDIAN_VAULT_PATH`, use `hermes config set terminal.env.KEY "value"` instead — it writes to `config.yaml` under the `terminal.env` section and persists across sessions.
