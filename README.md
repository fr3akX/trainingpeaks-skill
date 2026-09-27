# TrainingPeaks Skill

Read your training data, build structured workouts, and manage your TrainingPeaks calendar from a terminal or an AI assistant.

This repository includes a Python command-line tool and an [agent-facing skill guide](SKILL.md). You can use the CLI without an AI assistant. Authentication uses your own TrainingPeaks browser session; no developer API key is required.

> **Unofficial integration:** this tool uses TrainingPeaks' internal API. It is not affiliated with or endorsed by TrainingPeaks, and API changes may break functionality. Use it only with accounts and data you are authorized to access.

## Features

| Feature | What you can do |
| --- | --- |
| Athlete profile | Read account details and available sport thresholds. |
| Workout history and plans | List planned or completed sessions; inspect workout metrics, descriptions, comments, and interval structures. |
| Fitness and weekly load | Retrieve CTL, ATL, and TSB, plus a weekly training-load summary. |
| Health metrics | Read available HRV, resting heart rate, sleep, and weight data. |
| Personal records | Query cycling power/heart-rate records and running speed/heart-rate records. |
| Calendar editing | Create planned sessions, update metadata, replace intervals, move dates, or delete workouts. |
| Workout builder | Define power-based intervals in YAML, including warm-ups, recovery, cool-downs, and repeat blocks. Optionally estimate planned IF/TSS. |
| Comments | Add or delete workout comments. |
| Activity downloads | Download original activity files and periodically sync recent completed sessions locally. |
| Events and race priorities | Read events and ATP priorities through the Python API helper; there is no `events-atp` CLI command yet. |

Most data-reading commands support `--json` for scripting. Health data, records, and original activity files depend on what is available in your TrainingPeaks account.

## Requirements

- **Python 3.11 or newer** for the CLI and bundled test suite.
- **PyYAML** for YAML workout creation/editing and the full test suite. Other CLI functionality uses Python's standard library.
- A TrainingPeaks account, an internet connection, and a valid `Production_tpAuth` session cookie.
- Git if you want to clone the repository or receive updates.

The shell examples below use Bash on macOS/Linux. Run them from the repository directory unless noted otherwise.

## Quick start

### 1. Download and install

```bash
git clone https://github.com/fr3akX/trainingpeaks-skill.git
cd trainingpeaks-skill
python3 -m venv "$HOME/.venvs/trainingpeaks"
source "$HOME/.venvs/trainingpeaks/bin/activate"
python -m pip install PyYAML
python scripts/tp.py --help
```

The virtual environment is outside the repository so it will not be included in commits. Activate it again in each new terminal, or call its Python executable directly.

### 2. Authenticate locally

1. Sign in to [TrainingPeaks](https://app.trainingpeaks.com) in your browser.
2. Open Developer Tools, then **Application/Storage → Cookies → `app.trainingpeaks.com`**.
3. Copy the value of **`Production_tpAuth`**.
4. Run the following command in your own terminal and paste the cookie at the hidden prompt:

```bash
python -c '
from argparse import Namespace
from getpass import getpass
import sys
sys.path.insert(0, "scripts")
import tp
tp.cmd_auth(Namespace(cookie=getpass("Production_tpAuth: ")))
'
```

This invokes the same authentication routine as `tp.py auth`, without putting the cookie in shell history or command-line arguments. The CLI also accepts `auth <cookie>`, but avoid typing real secrets directly into command lines.

**Treat the cookie like a password.** Do not paste it into chat, commit it, or include it in screenshots or bug reports. Authentication output can contain your email and athlete ID; redact it before sharing.

Check the local authentication state, then verify a live request:

```bash
python scripts/tp.py auth-status
python scripts/tp.py profile
```

`auth-status` inspects local storage; it does not prove that TrainingPeaks still accepts your session. Tokens are refreshed automatically while the cookie remains valid. If the cookie expires, repeat the authentication steps.

For automation, `TP_AUTH_COOKIE` can be injected through a trusted secret manager or process environment. It takes precedence over the saved cookie when a token refresh is needed; the cached token and athlete configuration are still shared. Do not use it to switch accounts in an already-authenticated session without managing those caches separately.

## Everyday use

Dates below are illustrative. Replace them with the period you want to inspect, and use IDs returned from your own account.

```bash
# Planned and completed sessions in a date range
python scripts/tp.py workouts 2030-01-01 2030-01-07
python scripts/tp.py workouts 2030-01-01 2030-01-07 --filter completed --json

# Fitness, weekly load, health, and cycling records
python scripts/tp.py fitness --days 90
python scripts/tp.py week-summary
python scripts/tp.py health 2030-01-01 2030-01-07 --json
python scripts/tp.py peaks Bike power20min --days 365
python scripts/tp.py peaks Run speed5K --days 365

# Replace the placeholder with a workout ID from the list above
WORKOUT_ID="YOUR_WORKOUT_ID"
python scripts/tp.py workout "$WORKOUT_ID" --json
```

For available flags, append `--help` to any command:

```bash
python scripts/tp.py edit-meta --help
python scripts/tp.py create --help
```

### Edit an existing plan

Start with a preview. For commands that support it, remove `--dry-run` only after reviewing the proposed changes.

```bash
python scripts/tp.py edit-meta "$WORKOUT_ID" \
  --title "Easy endurance" --duration 1.5 --dry-run

python scripts/tp.py edit-date "$WORKOUT_ID" \
  --to 2030-01-08 --dry-run
```

CLI duration values are **hours**: for example, `--duration 1.5` means 90 minutes. Dates use `YYYY-MM-DD`.

To delete a planned workout, inspect it first, then run:

```bash
python scripts/tp.py workout "$WORKOUT_ID"
python scripts/tp.py delete "$WORKOUT_ID"
```

Deletion asks for confirmation. `--yes` skips that prompt; it is not a preview. There is no delete `--dry-run` option.

### Add or remove a comment

These commands write immediately; they do not support `--dry-run`.

```bash
python scripts/tp.py add-comment "$WORKOUT_ID" "Completed the planned intervals."
python scripts/tp.py workout "$WORKOUT_ID"

# Replace this placeholder with the comment ID shown in the workout
COMMENT_ID="YOUR_COMMENT_ID"
python scripts/tp.py delete-comment "$WORKOUT_ID" "$COMMENT_ID"
python scripts/tp.py workout "$WORKOUT_ID"
```

## Build a structured workout

Save this example as `intervals.yaml` using a text editor:

```yaml
steps:
  - { type: warmup, duration: 10min, ftp: 45-55 }
  - repeat: 3
    steps:
      - { type: active, duration: 5min, ftp: 90-95, name: "Work interval" }
      - { type: rest, duration: 3min, ftp: 45-55, name: "Recovery" }
  - { type: cooldown, duration: 10min, ftp: 40-50 }
```

`ftp` values are **percentages of FTP**, not watts. A single target such as `ftp: 90` is also supported. Durations accept seconds, minutes, hours, or a compound value such as `1h30min`. Repeat blocks cannot contain other repeat blocks.

Preview the new session:

```bash
python scripts/tp.py create \
  --date 2030-01-08 \
  --title "Example interval session" \
  --sport bike \
  --from-yaml intervals.yaml \
  --auto-tss \
  --dry-run
```

To create it, select the correct date and rerun without `--dry-run`. The command prints the new workout ID; inspect it with `workout <id>` and check the calendar in TrainingPeaks.

To replace the intervals in an existing planned session:

```bash
python scripts/tp.py edit-structure "$WORKOUT_ID" \
  --from-yaml intervals.yaml --auto-tss --dry-run
```

### How planned load works

- `--auto-tss` estimates IF and TSS from the YAML targets using modelled normalized power: a one-second power trace, 30-second rolling averages, and fourth-power weighting.
- These are **planned estimates**, not measured workout load or a guarantee of TrainingPeaks' own calculation.
- On creation, omitting `--auto-tss`, `--tss`, and `--if` leaves planned TSS/IF unset. Explicit `--tss` and `--if` override estimates.
- When editing a structure, omitting `--auto-tss` preserves existing planned TSS/IF; it does not clear them.
- Duration is derived from a supplied structure. Planned energy is not guessed from a fixed athlete FTP.

Unstructured sessions can be created with `--duration` and no YAML. Raw TrainingPeaks structure JSON is also supported through `--from-file`; automatic TSS estimation is only supported with YAML.

See the [full workout format](SKILL.md#dsl-format----from-yaml) and [planned-load reference](references/planned-tss-ownership.md) for details.

## Download and sync activity files

Store exports outside the repository; activity files can contain health data and GPS locations.

```bash
python scripts/tp.py download-fit "$WORKOUT_ID" \
  --output-dir "$HOME/trainingpeaks-exports"

python scripts/tp.py sync-fit \
  --days 7 --sport Bike \
  --output-dir "$HOME/trainingpeaks-exports" --once
```

`sync-fit` skips files that already exist. `--sport` accepts `Bike`, `Run`, or `All`. Without `--once`, it uses a local scheduling state file to decide whether a sync is due; it exits after each invocation rather than running as a background service. An external scheduler must invoke it periodically.

Optional cron wrappers are in `scripts/run_tp_sync_fit_cron.py`, `scripts/run_sync_fit_cron.py`, and `scripts/run_tp_sync_fit.sh`. They locate the CLI relative to themselves, accept additional sync arguments, and default to three days of history. Use an absolute output path and the correct Python environment in scheduled jobs.

## Use with an AI assistant

Install this folder using your assistant's local-skill installation mechanism, keeping `SKILL.md`, `scripts/`, and `references/` together. The assistant needs terminal access, the Python dependencies, and permission to use the authenticated account. Point it to the virtual environment's Python executable if its default interpreter does not have PyYAML.

Once configured, example requests include:

- “Show tomorrow's planned session and its interval targets.”
- “Compare this week's completed training with the plan.”
- “Review my last ride using its power, heart rate, TSS, and comments.”
- “Draft an interval workout and show me the dry-run before creating it.”

[SKILL.md](SKILL.md) is the detailed operating guide for the assistant; this README is the human getting-started guide. Agents running as the same operating-system user share the credential files below, even if they use separate agent profiles. Give access only to trusted agents and explicitly authorize calendar changes.

## Safety, privacy, and limitations

- **Back up important workouts before editing.** `workout <id> --json` can save a private snapshot. The raw `edit --from-file` command is an advanced full-object replacement, not a merge; use it cautiously.
- **A dry-run is not necessarily offline.** Create/edit previews do not send the calendar write, but may read the API or refresh authentication.
- **The completed-workout guard is limited.** Edit/delete commands refuse workouts with a non-null `tssActual` unless `--force` is passed. A completed activity without TSS may not trigger this guard. Inspect the workout rather than relying on it alone.
- **Verify writes.** PUT-based edits re-fetch the workout and compare changed non-structure fields; interval structures are not checked for exact equality. Creation and comment operations do not perform a full read-back verification. Inspect the resulting workout and calendar yourself.
- **Not every field has a CLI editor.** Feeling/RPE updates and event/ATP queries require the Python helpers described in [SKILL.md](SKILL.md) and the [events reference](references/events-atp-endpoint.md).
- **Sport IDs need care.** Check the intended sport in the current account before using `--type-id`, particularly for MTB, Walk, and Other.
- **Large queries have limits.** The `workouts` command restricts date spans to 90 days; fitness history is limited to 365 days. Split larger requests where appropriate.
- **Exports are private data.** Do not commit cookies, tokens, profile exports, workout JSON, or activity files. The repository's ignore rules are not a comprehensive privacy filter.
- **This is not a multi-account credential manager.** Isolate accounts with separate operating-system users or environments rather than sharing the same home-directory cache.

### Local files

| Path | Purpose |
| --- | --- |
| `~/.trainingpeaks/cookie` | Saved session cookie. |
| `~/.trainingpeaks/token.json` | Cached bearer token and expiry. |
| `~/.trainingpeaks/config.json` | Cached athlete ID and account information. |
| `~/.trainingpeaks_sync_fit.json` | Scheduling state for periodic FIT sync. |

Cookie and token files are stored unencrypted; the CLI attempts to restrict them to owner-only permissions (`0600`) where supported. The config file also contains personal data. Protect the containing directory and avoid sharing its contents.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| `No module named yaml` | Activate the intended environment and run `python -m pip install PyYAML`. |
| Authentication fails with HTTP 401 | Sign in again and authenticate with a fresh cookie. |
| `auth-status` says ready but a request fails | Local cache status is not a live authentication check. Try `profile` and inspect the actual error. |
| HTTP 500 from the token endpoint | This is a server-side error, not proof that your cookie is invalid or valid. Retry later and check service availability. |
| HTTP 403/404 | Check account access and whether the internal endpoint has changed. |
| An edit reports “No changes to apply” | The requested values may already match, or the field may not be covered by that CLI command. |
| FIT sync reports no new files | Files may already exist, the sport filter may exclude the activity, or the source file may be unavailable. Some failed per-workout downloads are skipped. |

## Development and tests

With PyYAML installed, run the offline test suite from the repository directory:

```bash
python -m unittest discover -s scripts -v
```

Tests cover the YAML parser, interval translation, planned-load estimation, account-neutral creation payloads, and portable sync wrappers. They do not require TrainingPeaks credentials or modify a live calendar.

Keep documentation examples synthetic and account-neutral. Before submitting a change, run the tests, inspect the diff for personal data, and keep real credentials and activity exports out of Git.
