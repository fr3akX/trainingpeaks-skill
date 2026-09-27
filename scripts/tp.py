#!/usr/bin/env python3
"""TrainingPeaks CLI — stdlib API access, with PyYAML for the workout DSL.

Provides access to the TrainingPeaks internal API using cookie-based auth.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import random
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ─── Constants ────────────────────────────────────────────────────────────────

TP_API_BASE = "https://tpapi.trainingpeaks.com"

TOKEN_ENDPOINT = "/users/v3/token"
USER_ENDPOINT = "/users/v3/user"
TOKEN_REFRESH_BUFFER = 60  # seconds before expiry to trigger refresh
MIN_REQUEST_INTERVAL = 0.15  # 150ms between requests
CONFIG_DIR = Path.home() / ".trainingpeaks"
COOKIE_FILE = CONFIG_DIR / "cookie"
TOKEN_FILE = CONFIG_DIR / "token.json"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Valid PR types by sport
BIKE_PR_TYPES = [
    "power5sec", "power1min", "power5min", "power10min", "power20min",
    "power60min", "power90min",
    "hR5sec", "hR1min", "hR5min", "hR10min", "hR20min", "hR60min", "hR90min",
]

RUN_PR_TYPES = [
    "hR5sec", "hR1min", "hR5min", "hR10min", "hR20min", "hR60min", "hR90min",
    "speed400Meter", "speed800Meter", "speed1K", "speed1Mi", "speed5K",
    "speed5Mi", "speed10K", "speed10Mi", "speedHalfMarathon", "speedMarathon",
    "speed50K",
]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def die(msg: str, code: int = 1) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def fmt_duration(hours) -> str:
    """Convert hours (float/None) to human-readable h:mm format.

    TrainingPeaks API returns totalTimePlanned/totalTime in hours (e.g. 1.5 = 1h30m).
    """
    if hours is None:
        return "—"
    h_float = float(hours)
    if h_float <= 0:
        return "—"
    total_minutes = round(h_float * 60)
    h, m = divmod(total_minutes, 60)
    if h:
        return f"{h}:{m:02d}"
    return f"0:{m:02d}"


# Workout type ID → sport name mapping (from TrainingPeaks API)
WORKOUT_TYPE_MAP = {
    1: "Swim",
    2: "Bike",
    3: "Run",
    4: "Brick",
    5: "Cross-Train",
    6: "Rest",
    7: "Walk",
    8: "Other",
    9: "Strength",
    10: "Custom",
    11: "Rowing",
    12: "XC Ski",
    13: "Mtn Bike",
}


def get_sport_name(workout) -> str:
    """Get sport name from a workout dict."""
    # Try workoutTypeFamilyId first (sometimes present)
    family = workout.get("workoutTypeFamilyId")
    if family and isinstance(family, str):
        return family
    # Fall back to workoutTypeValueId numeric mapping
    type_id = workout.get("workoutTypeValueId")
    if type_id is not None:
        return WORKOUT_TYPE_MAP.get(int(type_id), f"Type {type_id}")
    return "—"


def fmt_distance(meters) -> str:
    """Convert meters to km with 2 decimal places."""
    if meters is None:
        return "—"
    km = float(meters) / 1000
    if km < 0.01:
        return "—"
    return f"{km:.2f} km"


def fmt_float(val, decimals=1) -> str:
    if val is None:
        return "—"
    return f"{float(val):.{decimals}f}"


def print_table(headers: list[str], rows: list[list[str]], min_widths: list[int] | None = None) -> None:
    """Print a simple ASCII table."""
    if not rows:
        print("  (no data)")
        return
    widths = [len(h) for h in headers]
    if min_widths:
        widths = [max(w, m) for w, m in zip(widths, min_widths)]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(cell)))
    # Header
    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(header_line)
    print("  ".join("─" * widths[i] for i in range(len(headers))))
    # Rows
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            if i < len(widths):
                cells.append(str(cell).ljust(widths[i]))
            else:
                cells.append(str(cell))
        print("  ".join(cells))


# ─── Cookie / Token Storage ──────────────────────────────────────────────────

def get_cookie() -> str | None:
    """Get cookie from env var or file."""
    env = os.environ.get("TP_AUTH_COOKIE")
    if env:
        return env.strip()
    if COOKIE_FILE.exists():
        return COOKIE_FILE.read_text().strip()
    return None


def store_cookie(cookie: str) -> None:
    ensure_config_dir()
    COOKIE_FILE.write_text(cookie.strip())
    # Restrict permissions
    try:
        COOKIE_FILE.chmod(0o600)
    except OSError:
        pass


def load_token_cache() -> dict | None:
    """Load cached token from disk."""
    if not TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_FILE.read_text())
        return data
    except (json.JSONDecodeError, OSError):
        return None


def save_token_cache(access_token: str, expires_at: float) -> None:
    ensure_config_dir()
    TOKEN_FILE.write_text(json.dumps({
        "access_token": access_token,
        "expires_at": expires_at,
    }))
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


def clear_token_cache() -> None:
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(cfg: dict) -> None:
    ensure_config_dir()
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ─── HTTP Layer ───────────────────────────────────────────────────────────────

_last_request_time = 0.0


def _throttle() -> None:
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.monotonic()


def _http_request(url: str, method: str = "GET", headers: dict | None = None,
                  body: bytes | None = None) -> tuple[int, dict | list | None]:
    """Make an HTTP request, return (status_code, parsed_json_or_None)."""
    _throttle()
    if headers is None:
        headers = {}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = None
            return resp.status, data
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            data = None
        return e.code, data
    except urllib.error.URLError as e:
        die(f"Network error: {e.reason}")
    except Exception as e:
        die(f"Request failed: {e}")
    return 0, None  # unreachable


def exchange_cookie_for_token(cookie: str) -> dict:
    """Exchange Production_tpAuth cookie for OAuth token.

    Returns the full JSON response from /users/v3/token.
    """
    url = f"{TP_API_BASE}{TOKEN_ENDPOINT}"
    headers = {
        "Cookie": f"Production_tpAuth={cookie}",
        "Accept": "application/json",
    }
    status, data = _http_request(url, "GET", headers)
    if status == 401:
        die("Cookie expired or invalid. Re-authenticate with: tp.py auth <cookie>")
    if status != 200:
        die(f"Token exchange failed (HTTP {status})")
    if not isinstance(data, dict):
        die("Invalid token response format")
    return data


def ensure_token() -> str:
    """Ensure we have a valid Bearer token. Auto-refresh if needed.

    Returns the access_token string.
    """
    # Check cached token first
    cached = load_token_cache()
    if cached and cached.get("access_token"):
        expires_at = cached.get("expires_at", 0)
        if time.time() < (expires_at - TOKEN_REFRESH_BUFFER):
            return cached["access_token"]

    # Need to refresh — get cookie
    cookie = get_cookie()
    if not cookie:
        die("Not authenticated. Run: tp.py auth <cookie>")

    data = exchange_cookie_for_token(cookie)

    # The response may have token nested or at top level
    # From the MCP source: data["token"]["access_token"]
    token_data = data.get("token", data)
    access_token = token_data.get("access_token")
    if not access_token:
        die("Token response missing access_token")

    expires_in = token_data.get("expires_in", 3600)
    expires_at = time.time() + expires_in
    save_token_cache(access_token, expires_at)

    return access_token


def api_get(endpoint: str, params: dict | None = None) -> tuple[int, dict | list | None]:
    """Authenticated GET request."""
    token = ensure_token()
    url = f"{TP_API_BASE}{endpoint}"
    if params:
        qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        url = f"{url}?{qs}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    status, data = _http_request(url, "GET", headers)
    # On 401, try refreshing token once
    if status == 401:
        clear_token_cache()
        token = ensure_token()
        headers["Authorization"] = f"Bearer {token}"
        status, data = _http_request(url, "GET", headers)
    return status, data


def api_post(endpoint: str, body: dict | None = None) -> tuple[int, dict | list | None]:
    """Authenticated POST request."""
    token = ensure_token()
    url = f"{TP_API_BASE}{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    body_bytes = json.dumps(body).encode("utf-8") if body else None
    status, data = _http_request(url, "POST", headers, body_bytes)
    if status == 401:
        clear_token_cache()
        token = ensure_token()
        headers["Authorization"] = f"Bearer {token}"
        status, data = _http_request(url, "POST", headers, body_bytes)
    return status, data


def api_put(endpoint: str, body: dict) -> tuple[int, dict | list | None]:
    """Authenticated PUT request."""
    token = ensure_token()
    url = f"{TP_API_BASE}{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    body_bytes = json.dumps(body).encode("utf-8")
    status, data = _http_request(url, "PUT", headers, body_bytes)
    if status == 401:
        clear_token_cache()
        token = ensure_token()
        headers["Authorization"] = f"Bearer {token}"
        status, data = _http_request(url, "PUT", headers, body_bytes)
    return status, data


def api_delete(endpoint: str) -> tuple[int, dict | list | None]:
    """Authenticated DELETE request."""
    token = ensure_token()
    url = f"{TP_API_BASE}{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    status, data = _http_request(url, "DELETE", headers)
    if status == 401:
        clear_token_cache()
        token = ensure_token()
        headers["Authorization"] = f"Bearer {token}"
        status, data = _http_request(url, "DELETE", headers)
    return status, data


def _workout_url(workout_id: int | str) -> str:
    athlete_id = get_athlete_id()
    return f"/fitness/v6/athletes/{athlete_id}/workouts/{workout_id}"


def _now_iso() -> str:
    """Current time in TP's observed format (local, no timezone suffix)."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _diff_fields(old: dict, new: dict, fields: list[str]) -> list[tuple[str, object, object]]:
    """Return (field, old_val, new_val) triples for fields that changed."""
    out = []
    for k in fields:
        if old.get(k) != new.get(k):
            out.append((k, old.get(k), new.get(k)))
    return out


def mutate_workout(workout_id: int | str, mutator_fn, *,
                   force: bool = False, dry_run: bool = False,
                   verify_fields: list[str] | None = None) -> dict:
    """Read-modify-write on a single planned workout.

    1. GET current workout.
    2. Refuse if completed (tssActual != None) unless force=True.
    3. Call mutator_fn(dict) → returns mutated dict (same keys).
    4. If dry_run: print per-field diff, return.
    5. Update lastModifiedDate; stringify structure; PUT.
    6. Re-GET and verify fields in verify_fields match what was sent.

    Returns the verified workout dict on success. Exits via die() on failure.
    """
    url = _workout_url(workout_id)
    status, arr = api_get(url)
    # TP returns a list with one element for single-workout GET
    current = None
    if isinstance(arr, list) and arr:
        current = arr[0]
    elif isinstance(arr, dict):
        current = arr
    if status != 200 or not isinstance(current, dict):
        die(f"Workout {workout_id} not found (HTTP {status})", code=2)

    if current.get("tssActual") is not None and not force:
        die(
            f"Refusing to edit completed workout (tssActual={current['tssActual']}). "
            f"Pass --force to override.",
            code=2,
        )

    # Deep-copy via JSON round-trip so mutator can't accidentally edit current
    proposed = json.loads(json.dumps(current))
    proposed = mutator_fn(proposed)
    if not isinstance(proposed, dict):
        die(f"mutator_fn must return a dict, got {type(proposed).__name__}", code=2)

    if verify_fields is None:
        verify_fields = ["title", "description", "tssPlanned", "ifPlanned",
                         "totalTimePlanned", "workoutDay", "structure"]

    changes = _diff_fields(current, proposed, verify_fields)
    if not changes:
        print("No changes to apply.")
        return current

    if dry_run:
        print("Dry-run diff (no changes sent):")
        for k, old, new in changes:
            if k == "structure":
                print(f"  {k}: (structure changed — use --from-yaml to view proposed blocks)")
            else:
                print(f"  {k}: {old!r} → {new!r}")
        return current

    # Finalize PUT payload
    proposed["lastModifiedDate"] = _now_iso()
    if isinstance(proposed.get("structure"), dict):
        proposed["structure"] = json.dumps(proposed["structure"])

    put_status, _ = api_put(url, proposed)
    if put_status < 200 or put_status >= 300:
        die(f"PUT failed with HTTP {put_status}", code=3)

    # Verify
    vstatus, varr = api_get(url)
    verified = varr[0] if isinstance(varr, list) and varr else (varr if isinstance(varr, dict) else None)
    if vstatus != 200 or not isinstance(verified, dict):
        die(f"Post-PUT verify GET failed (HTTP {vstatus})", code=4)

    mismatches = []
    for k, _, new in changes:
        if k == "structure":
            continue  # stringified shape differs between request/response; skip strict equality
        if verified.get(k) != new:
            mismatches.append((k, new, verified.get(k)))
    if mismatches:
        print("Verification mismatch after PUT:")
        for k, expected, actual in mismatches:
            print(f"  {k}: expected {expected!r}, got {actual!r}")
        sys.exit(4)

    changed_names = ", ".join(c[0] for c in changes)
    print(f"✓ verified: {changed_names}")
    return verified


# ─── Athlete ID ───────────────────────────────────────────────────────────────

def get_athlete_id() -> int:
    """Get athlete ID, using cache or fetching from API."""
    cfg = load_config()
    aid = cfg.get("athlete_id")
    if aid:
        return int(aid)

    # Fetch from profile
    status, data = api_get(USER_ENDPOINT)
    if status != 200 or not isinstance(data, dict):
        die("Failed to fetch user profile")

    user_data = data.get("user", data)
    athlete_id = user_data.get("personId")
    if not athlete_id:
        athletes = user_data.get("athletes", [])
        if athletes:
            athlete_id = athletes[0].get("athleteId")
    if not athlete_id:
        die("Could not determine athlete ID from profile")

    cfg["athlete_id"] = int(athlete_id)
    save_config(cfg)
    return int(athlete_id)


# ─── Commands ─────────────────────────────────────────────────────────────────

def cmd_auth(args: argparse.Namespace) -> None:
    """Store and validate a Production_tpAuth cookie."""
    cookie = args.cookie.strip()
    if not cookie:
        die("Cookie value cannot be empty")

    print("Validating cookie…")
    data = exchange_cookie_for_token(cookie)

    # Extract token info
    token_data = data.get("token", data)
    access_token = token_data.get("access_token")
    if not access_token:
        die("Token exchange succeeded but no access_token in response")

    expires_in = token_data.get("expires_in", 3600)
    expires_at = time.time() + expires_in

    # Store everything
    store_cookie(cookie)
    save_token_cache(access_token, expires_at)

    # Try to get and cache athlete ID
    athlete_id = data.get("athleteId")
    username = data.get("username")

    if athlete_id:
        cfg = load_config()
        cfg["athlete_id"] = int(athlete_id)
        if username:
            cfg["email"] = username
        save_config(cfg)

    print("✓ Authenticated successfully!")
    if username:
        print(f"  Account: {username}")
    if athlete_id:
        print(f"  Athlete ID: {athlete_id}")
    print(f"  Token expires in: {expires_in // 60} minutes")
    print(f"  Credentials stored in: {CONFIG_DIR}")


def cmd_auth_status(args: argparse.Namespace) -> None:
    """Check authentication status."""
    cookie = get_cookie()
    if not cookie:
        print("✗ Not authenticated")
        print("  Run: tp.py auth <cookie>")
        sys.exit(1)

    source = "environment variable" if os.environ.get("TP_AUTH_COOKIE") else "file"
    print(f"Cookie: stored ({source})")

    cached = load_token_cache()
    if cached and cached.get("access_token"):
        expires_at = cached.get("expires_at", 0)
        remaining = expires_at - time.time()
        if remaining > TOKEN_REFRESH_BUFFER:
            mins = int(remaining // 60)
            print(f"Token: valid ({mins}m remaining)")
        else:
            print("Token: expired (will auto-refresh)")
    else:
        print("Token: not cached (will fetch on next request)")

    cfg = load_config()
    if cfg.get("athlete_id"):
        print(f"Athlete ID: {cfg['athlete_id']}")
    if cfg.get("email"):
        print(f"Account: {cfg['email']}")

    print("✓ Ready")


def cmd_profile(args: argparse.Namespace) -> None:
    """Get athlete profile."""
    status, data = api_get(USER_ENDPOINT)
    if status != 200 or not isinstance(data, dict):
        die(f"Failed to fetch profile (HTTP {status})")

    if args.json:
        print(json.dumps(data, indent=2))
        return

    user = data.get("user", data)
    print("Profile")
    print("═" * 40)
    print(f"  Name:        {user.get('firstName', '')} {user.get('lastName', '')}")
    print(f"  Email:       {user.get('username', '—')}")
    print(f"  Athlete ID:  {user.get('personId', user.get('athleteId', '—'))}")
    print(f"  Account:     {user.get('accountType', '—')}")

    athletes = user.get("athletes", [])
    if athletes:
        a = athletes[0]
        print(f"  Weight:      {a.get('weight', '—')} kg")
        print(f"  DOB:         {a.get('dateOfBirth', '—')}")
        print(f"  Gender:      {a.get('sex', '—')}")
        print(f"  Bike FTP:    {a.get('cyclingFtp', '—')} W")
        print(f"  Run FTP:     {a.get('runningFtp', '—')}")
        print(f"  Swim FTP:    {a.get('swimFtp', '—')}")


def cmd_workouts(args: argparse.Namespace) -> None:
    """List workouts in a date range."""
    try:
        start = date.fromisoformat(args.start_date)
        end = date.fromisoformat(args.end_date)
    except ValueError as e:
        die(f"Invalid date: {e}. Use YYYY-MM-DD format.")

    if start > end:
        die("start_date must be before or equal to end_date")
    if (end - start).days > 90:
        die("Date range too large. Maximum is 90 days.")

    athlete_id = get_athlete_id()
    endpoint = f"/fitness/v6/athletes/{athlete_id}/workouts/{start}/{end}"
    status, data = api_get(endpoint)

    if status != 200:
        die(f"Failed to fetch workouts (HTTP {status})")

    if not isinstance(data, list):
        data = []

    # Apply filter
    workouts = data
    if args.filter == "completed":
        workouts = [w for w in workouts if w.get("completed") or w.get("totalTime") is not None]
    elif args.filter == "planned":
        workouts = [w for w in workouts if not w.get("completed") and w.get("totalTime") is None]

    if args.json:
        print(json.dumps(workouts, indent=2))
        return

    if not workouts:
        print(f"No {'matching ' if args.filter != 'all' else ''}workouts found for {start} → {end}")
        return

    print(f"Workouts: {start} → {end}  ({len(workouts)} {'total' if args.filter == 'all' else args.filter})")
    print()

    headers = ["Date", "Title", "Sport", "Status", "Planned", "Actual", "TSS", "Distance"]
    rows = []
    for w in workouts:
        wo_date = (w.get("workoutDay") or "")[:10]
        title = w.get("title") or "—"
        if len(title) > 30:
            title = title[:27] + "…"
        sport = get_sport_name(w)
        is_completed = w.get("completed") or w.get("totalTime") is not None
        status_str = "✓" if is_completed else "○"
        dur_planned = fmt_duration(w.get("totalTimePlanned"))
        dur_actual = fmt_duration(w.get("totalTime"))
        tss = fmt_float(w.get("tssActual") or w.get("tssPlanned"), 0)
        dist = fmt_distance(w.get("distance") or w.get("distancePlanned"))

        rows.append([wo_date, title, sport, status_str, dur_planned, dur_actual, tss, dist])

    print_table(headers, rows)


def cmd_workout(args: argparse.Namespace) -> None:
    """Get full workout detail."""
    athlete_id = get_athlete_id()
    endpoint = f"/fitness/v6/athletes/{athlete_id}/workouts/{args.workout_id}"
    status, data = api_get(endpoint)

    if status == 404:
        die(f"Workout {args.workout_id} not found")
    if status != 200 or not isinstance(data, dict):
        die(f"Failed to fetch workout (HTTP {status})")

    if args.json:
        print(json.dumps(data, indent=2))
        return

    w = data
    print(f"Workout: {w.get('title', 'Untitled')}")
    print("═" * 50)
    print(f"  Date:         {(w.get('workoutDay') or '')[:10]}")
    print(f"  Sport:        {get_sport_name(w)}")
    print(f"  Status:       {'Completed ✓' if w.get('completed') or w.get('totalTime') else 'Planned ○'}")
    print()

    # Durations
    print("  Duration")
    print(f"    Planned:    {fmt_duration(w.get('totalTimePlanned'))}")
    print(f"    Actual:     {fmt_duration(w.get('totalTime'))}")
    print()

    # Metrics
    print("  Metrics")
    print(f"    TSS:        {fmt_float(w.get('tssActual'), 0)} actual / {fmt_float(w.get('tssPlanned'), 0)} planned")
    print(f"    IF:         {fmt_float(w.get('if'), 2)} actual / {fmt_float(w.get('ifPlanned'), 2)} planned")
    print(f"    Distance:   {fmt_distance(w.get('distance'))} actual / {fmt_distance(w.get('distancePlanned'))} planned")
    print(f"    Avg Power:  {fmt_float(w.get('powerAverage'), 0)} W")
    print(f"    NP:         {fmt_float(w.get('normalizedPowerActual'), 0)} W")
    print(f"    Avg HR:     {fmt_float(w.get('heartRateAverage'), 0)} bpm")
    print(f"    Avg Cadence:{fmt_float(w.get('cadenceAverage'), 0)}")
    print(f"    Elev Gain:  {fmt_float(w.get('elevationGain'), 0)} m")
    print(f"    Calories:   {w.get('calories') or '—'}")
    print()

    # Description
    desc = w.get("description")
    if desc:
        print("  Description:")
        for line in desc.strip().split("\n"):
            print(f"    {line}")
        print()

    coach = w.get("coachComments")
    if coach:
        print("  Coach Comments:")
        for line in coach.strip().split("\n"):
            print(f"    {line}")
        print()

    athlete = w.get("athleteComments")
    if athlete:
        print("  Athlete Comments:")
        for line in athlete.strip().split("\n"):
            print(f"    {line}")


def cmd_fitness(args: argparse.Namespace) -> None:
    """Get CTL/ATL/TSB fitness data."""
    days = args.days
    if days < 1 or days > 365:
        die("--days must be between 1 and 365")

    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    athlete_id = get_athlete_id()
    endpoint = f"/fitness/v1/athletes/{athlete_id}/reporting/performancedata/{start_date}/{end_date}"
    body = {
        "atlConstant": 7,
        "atlStart": 0,
        "ctlConstant": 42,
        "ctlStart": 0,
        "workoutTypes": [],
    }

    status, data = api_post(endpoint, body)
    if status != 200:
        die(f"Failed to fetch fitness data (HTTP {status})")

    if not isinstance(data, list):
        data = []

    if args.json:
        print(json.dumps(data, indent=2))
        return

    if not data:
        print("No fitness data available")
        return

    # Current values (latest entry)
    latest = data[-1]
    ctl = latest.get("ctl", 0)
    atl = latest.get("atl", 0)
    tsb = latest.get("tsb", 0)

    def fitness_status(tsb_val: float) -> str:
        if tsb_val > 25:
            return "Very Fresh (detraining risk)"
        elif tsb_val > 10:
            return "Fresh (race ready)"
        elif tsb_val > 0:
            return "Neutral (normal training)"
        elif tsb_val > -10:
            return "Tired (absorbing training)"
        elif tsb_val > -25:
            return "Very Tired (high fatigue)"
        else:
            return "Exhausted (overreaching risk)"

    print("Fitness Summary")
    print("═" * 45)
    print(f"  CTL (Fitness):  {ctl:.1f}")
    print(f"  ATL (Fatigue):  {atl:.1f}")
    print(f"  TSB (Form):     {tsb:.1f}")
    print(f"  Status:         {fitness_status(tsb)}")
    print(f"  Period:         {start_date} → {end_date} ({days} days)")
    print()

    # Show last 14 days of detail (or all if fewer)
    display_data = data[-14:] if len(data) > 14 else data
    print(f"Daily Data (last {len(display_data)} days):")
    headers = ["Date", "TSS", "CTL", "ATL", "TSB"]
    rows = []
    for entry in display_data:
        d = (entry.get("workoutDay") or "")[:10]
        tss = fmt_float(entry.get("tssActual", 0), 0)
        c = fmt_float(entry.get("ctl", 0), 1)
        a = fmt_float(entry.get("atl", 0), 1)
        t = fmt_float(entry.get("tsb", 0), 1)
        rows.append([d, tss, c, a, t])

    print_table(headers, rows)

    if len(data) > 14:
        print(f"\n  (Showing last 14 of {len(data)} days. Use --json for full data)")


def cmd_peaks(args: argparse.Namespace) -> None:
    """Get personal records."""
    sport = args.sport
    pr_type = args.pr_type
    days = args.days

    # Validate
    valid_types = BIKE_PR_TYPES if sport == "Bike" else RUN_PR_TYPES
    if pr_type not in valid_types:
        die(f"Invalid pr_type '{pr_type}' for {sport}.\n"
            f"Valid types: {', '.join(valid_types)}")

    athlete_id = get_athlete_id()
    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    endpoint = f"/personalrecord/v2/athletes/{athlete_id}/{sport}"
    params = {
        "prType": pr_type,
        "startDate": f"{start_date}T00:00:00",
        "endDate": f"{end_date}T00:00:00",
    }

    status, data = api_get(endpoint, params)
    if status != 200:
        die(f"Failed to fetch personal records (HTTP {status})")

    if not isinstance(data, list):
        data = []

    if args.json:
        print(json.dumps(data, indent=2))
        return

    if not data:
        print(f"No {pr_type} records found for {sport} in the last {days} days")
        return

    # Determine unit
    is_power = "power" in pr_type.lower()
    is_speed = "speed" in pr_type.lower()
    is_hr = "hR" in pr_type or "hr" in pr_type.lower()

    if is_power:
        unit = "W"
    elif is_hr:
        unit = "bpm"
    elif is_speed:
        unit = ""  # pace or speed — varies
    else:
        unit = ""

    print(f"Personal Records: {sport} — {pr_type}")
    print(f"Period: {start_date} → {end_date}")
    print("═" * 55)

    headers = ["Rank", f"Value ({unit})" if unit else "Value", "Date", "Workout"]
    rows = []
    for r in data:
        rank = str(r.get("rank", "—"))
        value = r.get("value")
        if value is not None:
            if is_power:
                value_str = f"{int(float(value))}"
            elif is_hr:
                value_str = f"{int(float(value))}"
            else:
                value_str = fmt_float(value, 2)
        else:
            value_str = "—"
        wo_date = (r.get("workoutDate") or "")[:10]
        wo_title = r.get("workoutTitle") or "—"
        if len(wo_title) > 30:
            wo_title = wo_title[:27] + "…"
        rows.append([rank, value_str, wo_date, wo_title])

    print_table(headers, rows)


def cmd_week_summary(args: argparse.Namespace) -> None:
    """Print this week's TSS total + current CTL/ATL/TSB in one shot."""
    today = date.today()
    # Start of the current week (Monday)
    week_start = today - timedelta(days=today.weekday())
    week_end = today

    athlete_id = get_athlete_id()

    # ── 1. Fetch workouts for this week ──────────────────────────────────────
    wo_status, workouts = api_get(
        f"/fitness/v6/athletes/{athlete_id}/workouts/{week_start}/{week_end}"
    )
    if wo_status != 200 or not isinstance(workouts, list):
        workouts = []

    # ── 2. Fetch fitness data (CTL/ATL/TSB) ──────────────────────────────────
    # Use 90 days so the rolling averages are accurate
    fit_start = today - timedelta(days=90)
    fit_endpoint = (
        f"/fitness/v1/athletes/{athlete_id}/reporting/performancedata"
        f"/{fit_start}/{today}"
    )
    fit_body = {
        "atlConstant": 7,
        "atlStart": 0,
        "ctlConstant": 42,
        "ctlStart": 0,
        "workoutTypes": [],
    }
    fit_status, fit_data = api_post(fit_endpoint, fit_body)
    if fit_status != 200 or not isinstance(fit_data, list) or not fit_data:
        fit_data = []

    # Current values
    ctl = atl = tsb = None
    if fit_data:
        latest = fit_data[-1]
        ctl = latest.get("ctl")
        atl = latest.get("atl")
        tsb = latest.get("tsb")

    def fitness_status(tsb_val) -> str:
        if tsb_val is None:
            return "—"
        t = float(tsb_val)
        if t > 25:   return "Very Fresh"
        if t > 10:   return "Fresh / Race Ready"
        if t > 0:    return "Neutral"
        if t > -10:  return "Tired"
        if t > -25:  return "Very Tired"
        return "Exhausted / Overreaching"

    # ── 3. Aggregate weekly TSS by sport ────────────────────────────────────
    sport_tss: dict[str, float] = {}
    sport_dur: dict[str, float] = {}
    total_tss = 0.0

    for w in workouts:
        tss_val = w.get("tssActual") or 0
        dur_val = w.get("totalTime") or 0
        sport = get_sport_name(w)
        sport_tss[sport] = sport_tss.get(sport, 0) + float(tss_val)
        sport_dur[sport] = sport_dur.get(sport, 0) + float(dur_val)
        total_tss += float(tss_val)

    days_done = today.weekday() + 1  # Mon=1 … Sun=7
    days_left = 7 - days_done

    # ── 4. Print summary ─────────────────────────────────────────────────────
    if args.json:
        out = {
            "week_start": str(week_start),
            "week_end": str(week_end),
            "total_tss": round(total_tss, 1),
            "by_sport": {s: {"tss": round(t, 1), "hours": round(sport_dur.get(s, 0), 2)}
                         for s, t in sport_tss.items()},
            "ctl": round(float(ctl), 1) if ctl is not None else None,
            "atl": round(float(atl), 1) if atl is not None else None,
            "tsb": round(float(tsb), 1) if tsb is not None else None,
        }
        print(json.dumps(out, indent=2))
        return

    print(f"Week Summary  {week_start} → {week_end}")
    print("═" * 45)
    print()

    print("  Training Load")
    print(f"    CTL (Fitness):  {fmt_float(ctl, 1)}")
    print(f"    ATL (Fatigue):  {fmt_float(atl, 1)}")
    print(f"    TSB (Form):     {fmt_float(tsb, 1)}  — {fitness_status(tsb)}")
    print()

    print(f"  Week TSS: {total_tss:.0f}  ({days_done} day{'s' if days_done != 1 else ''} done, {days_left} to go)")
    if sport_tss:
        for sport, tss_s in sorted(sport_tss.items(), key=lambda x: -x[1]):
            hrs = sport_dur.get(sport, 0)
            h, m = divmod(round(hrs * 60), 60)
            dur_str = f"{h}:{m:02d}" if h else f"0:{m:02d}"
            print(f"    {sport:<12} {tss_s:>5.0f} TSS   {dur_str} h")
    else:
        print("    No completed workouts yet this week")
    print()


def cmd_health(args: argparse.Namespace) -> None:
    """Get consolidated health metrics (HRV, RHR, weight, etc.)."""
    try:
        start = date.fromisoformat(args.start_date)
        end = date.fromisoformat(args.end_date)
    except ValueError as e:
        die(f"Invalid date: {e}. Use YYYY-MM-DD format.")

    if start > end:
        die("start_date must be before or equal to end_date")

    athlete_id = get_athlete_id()
    endpoint = f"/metrics/v3/athletes/{athlete_id}/consolidatedtimedmetrics/{start}/{end}"
    status, data = api_get(endpoint)

    if status != 200:
        die(f"Failed to fetch health metrics (HTTP {status})")

    if args.json:
        print(json.dumps(data, indent=2))
        return

    # Minimal summary if possible
    if not data:
        print("No health metrics returned")
        return

    print(f"Health metrics: {start} → {end}")
    print(json.dumps(data, indent=2))


def cmd_download_fit(args: argparse.Namespace) -> None:
    """Download raw workout FIT file for a given workout.

    NOTE: Requires a file id for now (from TrainingPeaks UI/network). Once a
    file listing endpoint is known, this can be extended to auto-discover it.
    """
    athlete_id = get_athlete_id()
    workout_id = args.workout_id
    file_id = args.file_id

    # If file id is not provided, try to discover it via the workout details
    if not file_id:
        status, details = api_get(f"/fitness/v6/athletes/{athlete_id}/workouts/{workout_id}/details")
        if status != 200 or not isinstance(details, dict):
            die(f"Failed to load workout details for {workout_id} (status {status})")
        infos = details.get("workoutDeviceFileInfos") or []
        if not infos:
            die(f"No device files found for workout {workout_id}")
        # Prefer FIT/FIT.GZ files if present
        chosen = None
        for info in infos:
            name = (info.get("fileName") or "").lower()
            if name.endswith(".fit") or name.endswith(".fit.gz"):
                chosen = info
                break
        if not chosen:
            # fall back to first entry
            chosen = infos[0]
        file_id = chosen.get("fileId")
        if not file_id:
            die(f"Workout {workout_id} has device files but no fileId field")

    token = ensure_token()
    url = f"{TP_API_BASE}/fitness/v6/athletes/{athlete_id}/workouts/{workout_id}/rawfiledata/{file_id}"
    headers = {"Authorization": f"Bearer {token}"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        die(f"Failed to download FIT file (HTTP {e.code}): {msg[:200]}")
    except Exception as e:
        die(f"Failed to download FIT file: {e}")

    # Determine output path
    out_dir = Path(args.output_dir) if args.output_dir else Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)
    # Default filename: <workoutId>-<fileId>.fit.gz
    fname = args.output_name or f"{workout_id}-{file_id}.fit.gz"
    out_path = out_dir / fname
    out_path.write_bytes(data)
    print(f"✓ Saved FIT file to {out_path}")


def cmd_sync_fit(args: argparse.Namespace) -> None:
    """Sync recent completed workouts' FIT files into a local directory.

    Intended usage:
    - Run manually when needed (with --once) OR
    - Trigger periodically (e.g. every minute) and let this command decide,
      via a randomised state file, whether a real sync is due. This yields an
      effective random interval between syncs (e.g. 1–10 minutes).
    """
    athlete_id = get_athlete_id()

    # Randomised scheduling state
    state_path = Path.home() / ".trainingpeaks_sync_fit.json"
    now = time.time()
    if not args.once:
        state: dict[str, object] = {}
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
            except Exception:
                state = {}
        next_sync = float(state.get("next_sync_epoch", 0) or 0)
        if now < next_sync:
            # Not time yet; exit quickly
            # (safe for frequent triggers like a cron job every minute)
            return

    days = args.days
    if days < 1 or days > 365:
        die("--days must be between 1 and 365")

    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)

    endpoint = f"/fitness/v6/athletes/{athlete_id}/workouts/{start_date}/{end_date}"
    status, workouts = api_get(endpoint)
    if status != 200 or not isinstance(workouts, list):
        die(f"Failed to fetch workouts for sync (status {status})")

    out_dir = Path(args.output_dir or "fit_exports")
    out_dir.mkdir(parents=True, exist_ok=True)

    sport_filter = (args.sport or "").lower()
    new_files = 0

    for w in workouts:
        wid = w.get("workoutId")
        if not wid:
            continue

        # Filter by sport if requested
        sport_name = get_sport_name(w).lower()
        if sport_filter and sport_filter != "all" and sport_name != sport_filter:
            continue

        # Only completed workouts by default
        if not (w.get("completed") or w.get("totalTime") is not None):
            continue

        # Fetch details to discover device files
        status_d, details = api_get(f"/fitness/v6/athletes/{athlete_id}/workouts/{wid}/details")
        if status_d != 200 or not isinstance(details, dict):
            continue
        infos = details.get("workoutDeviceFileInfos") or []
        if not infos:
            continue

        chosen = None
        for info in infos:
            name = (info.get("fileName") or "").lower()
            if name.endswith(".fit") or name.endswith(".fit.gz"):
                chosen = info
                break
        if not chosen:
            chosen = infos[0]

        file_id = chosen.get("fileId")
        if not file_id:
            continue

        default_name = f"{wid}-{file_id}.fit.gz"
        fname = default_name
        dest = out_dir / fname
        if dest.exists():
            # Already synced
            continue

        token = ensure_token()
        url = f"{TP_API_BASE}/fitness/v6/athletes/{athlete_id}/workouts/{wid}/rawfiledata/{file_id}"
        headers = {"Authorization": f"Bearer {token}"}
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
        except Exception:
            continue

        dest.write_bytes(data)
        new_files += 1
        print(f"Synced workout {wid} -> {dest}")

    if new_files == 0:
        print("Sync complete. No new FIT files.")
    else:
        print(f"Sync complete. New FIT files: {new_files}")

    # Update next sync time if using random scheduling
    if not args.once:
        min_m = args.min_interval_min
        max_m = args.max_interval_min
        if min_m < 1:
            min_m = 1
        if max_m < min_m:
            max_m = min_m
        delay_s = random.uniform(min_m * 60, max_m * 60)
        state = {"next_sync_epoch": now + delay_s}
        try:
            state_path.write_text(json.dumps(state))
        except Exception:
            pass


def cmd_add_comment(args: argparse.Namespace) -> None:
    """Add a comment to a workout."""
    athlete_id = get_athlete_id()
    workout_id = args.workout_id
    endpoint = f"/fitness/v2/athletes/{athlete_id}/workouts/{workout_id}/comments"
    status, data = api_post(endpoint, {"value": args.comment})
    if status != 200:
        die(f"Failed to add comment (HTTP {status})")
    # Response is the full comments list — find the newest one
    if isinstance(data, list) and data:
        newest = max(data, key=lambda c: c.get("id", 0))
        print(f"✓ Comment added (id {newest['id']}): {newest['comment']}")
    else:
        print("✓ Comment added.")


def cmd_delete_comment(args: argparse.Namespace) -> None:
    """Delete a comment from a workout."""
    athlete_id = get_athlete_id()
    workout_id = args.workout_id
    comment_id = args.comment_id
    token = ensure_token()
    url = f"{TP_API_BASE}/fitness/v2/athletes/{athlete_id}/workouts/{workout_id}/comments/{comment_id}"
    headers = {"Authorization": f"Bearer {token}"}
    req = urllib.request.Request(url, headers=headers, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status == 200:
                print(f"✓ Comment {comment_id} deleted.")
            else:
                die(f"Unexpected status {resp.status}")
    except urllib.error.HTTPError as e:
        die(f"Failed to delete comment (HTTP {e.code}): {e.read().decode()[:200]}")
    except urllib.error.URLError as e:
        die(f"Failed to delete comment: {e}")


def cmd_edit_meta(args: argparse.Namespace) -> None:
    """Edit metadata (title, description, TSS, IF, duration, energy) on a planned workout."""
    updates: dict = {}
    if args.title is not None:
        updates["title"] = args.title
    if args.description is not None:
        updates["description"] = args.description
    if args.tss is not None:
        updates["tssPlanned"] = float(args.tss)
    if args.if_ is not None:
        updates["ifPlanned"] = float(args.if_)
    if args.duration is not None:
        updates["totalTimePlanned"] = float(args.duration)
    if args.energy is not None:
        updates["energyPlanned"] = float(args.energy)

    if not updates:
        die("No fields to update. Pass at least one of --title, --description, "
            "--tss, --if, --duration, --energy.", code=2)

    def mutator(w: dict) -> dict:
        w.update(updates)
        return w

    verify = list(updates.keys())
    mutate_workout(args.workout_id, mutator,
                   force=args.force, dry_run=args.dry_run,
                   verify_fields=verify)


def cmd_edit_structure(args: argparse.Namespace) -> None:
    """Replace the interval structure of a planned workout."""
    if args.from_yaml and args.from_file:
        die("Pass exactly one of --from-yaml or --from-file", code=2)
    if not args.from_yaml and not args.from_file:
        die("Pass --from-yaml FILE or --from-file FILE", code=2)

    # Import lazily so tp.py still runs if PyYAML isn't installed for other commands
    import workout_dsl

    if args.from_yaml:
        try:
            yaml_text = Path(args.from_yaml).read_text(encoding="utf-8")
        except OSError as e:
            die(f"Cannot read {args.from_yaml}: {e}", code=2)
        try:
            blocks = workout_dsl.parse_dsl(yaml_text)
        except ValueError as e:
            die(f"DSL error: {e}", code=2)
        structure = workout_dsl.to_tp_structure(blocks)
        est_if, est_tss = workout_dsl.estimate_tss_if(blocks)
        print(f"Estimated from structure: IF={est_if}, TSS={est_tss}")
    else:  # --from-file (raw TP structure JSON)
        try:
            raw = Path(args.from_file).read_text(encoding="utf-8")
        except OSError as e:
            die(f"Cannot read {args.from_file}: {e}", code=2)
        try:
            structure = json.loads(raw)
        except json.JSONDecodeError as e:
            die(f"Invalid JSON in {args.from_file}: {e}", code=2)
        if not isinstance(structure, dict) or "structure" not in structure:
            die("Raw structure file must be a dict with a 'structure' key", code=2)
        est_if = est_tss = None

    # Derive totalTimePlanned hours from the structure's total duration
    total_s = 0
    for blk in structure.get("structure", []):
        total_s += int(blk.get("end", 0)) - int(blk.get("begin", 0))
    duration_h = total_s / 3600.0

    def mutator(w: dict) -> dict:
        w["structure"] = structure
        w["totalTimePlanned"] = duration_h
        if est_if is not None and args.auto_tss:
            w["ifPlanned"] = est_if
            w["tssPlanned"] = est_tss
        return w

    verify = ["structure", "totalTimePlanned"]
    if est_if is not None and args.auto_tss:
        verify += ["ifPlanned", "tssPlanned"]

    mutate_workout(args.workout_id, mutator,
                   force=args.force, dry_run=args.dry_run,
                   verify_fields=verify)


def cmd_edit_date(args: argparse.Namespace) -> None:
    """Move a planned workout to a different date."""
    # Validate date format
    try:
        target = datetime.strptime(args.to, "%Y-%m-%d").date()
    except ValueError:
        die(f"--to must be YYYY-MM-DD, got {args.to!r}", code=2)

    new_day = f"{target.isoformat()}T00:00:00"

    def mutator(w: dict) -> dict:
        w["workoutDay"] = new_day
        # Also nudge startTimePlanned if present (preserve time of day)
        start = w.get("startTimePlanned")
        if isinstance(start, str) and "T" in start:
            _, tail = start.split("T", 1)
            w["startTimePlanned"] = f"{target.isoformat()}T{tail}"
        return w

    mutate_workout(args.workout_id, mutator,
                   force=args.force, dry_run=args.dry_run,
                   verify_fields=["workoutDay"])


def cmd_delete(args: argparse.Namespace) -> None:
    """Delete a planned workout."""
    url = _workout_url(args.workout_id)
    status, arr = api_get(url)
    current = arr[0] if isinstance(arr, list) and arr else (arr if isinstance(arr, dict) else None)
    if status != 200 or not isinstance(current, dict):
        die(f"Workout {args.workout_id} not found (HTTP {status})", code=2)

    if current.get("tssActual") is not None and not args.force:
        die(
            f"Refusing to delete completed workout (tssActual={current['tssActual']}). "
            f"Pass --force to override.",
            code=2,
        )

    title = current.get("title", "?")
    day = str(current.get("workoutDay", "?")).split("T")[0]

    if not args.yes:
        try:
            resp = input(f"Delete '{title}' on {day}? [y/N]: ")
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(130)
        if resp.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            sys.exit(130)

    d_status, _ = api_delete(url)
    if d_status < 200 or d_status >= 300:
        die(f"DELETE failed with HTTP {d_status}", code=3)

    # Verify it's gone
    vstatus, _ = api_get(url)
    if vstatus == 200:
        die("Post-DELETE verify shows workout still present", code=4)

    print(f"✓ deleted: '{title}' on {day}")


# Built-in sport defaults, kept consistent with WORKOUT_TYPE_MAP.
# Verify the displayed sport against the current account/API before relying on
# these labels. Use --type-id with a verified ID for any unsupported sport.
_SPORT_IDS = {
    "bike":     2,   # confirmed
    "run":      3,   # confirmed
    "swim":     1,   # confirmed
    "strength": 9,   # confirmed
    "walk":     7,   # confirmed
    "other":    8,   # confirmed
}


def _resolve_workout_type_id(args: argparse.Namespace) -> int:
    """Resolve sport name / raw type-id from CLI args into an integer."""
    if args.type_id is not None:
        return args.type_id
    sport = args.sport or "bike"
    try:
        return _SPORT_IDS[sport]
    except KeyError:
        die(f"Unknown sport {sport!r}. Valid: {', '.join(_SPORT_IDS)}", code=2)


def cmd_create(args: argparse.Namespace) -> None:
    """Create a new planned workout via POST."""
    # Validate date format
    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        die(f"--date must be YYYY-MM-DD, got {args.date!r}", code=2)

    if not args.title or not args.title.strip():
        die("--title must be a non-empty string", code=2)

    if args.from_yaml and args.from_file:
        die("Pass at most one of --from-yaml or --from-file", code=2)

    sport_id = _resolve_workout_type_id(args)

    # Build structure (optional)
    structure: dict | None = None
    est_if = est_tss = None
    if args.from_yaml:
        import workout_dsl
        try:
            yaml_text = Path(args.from_yaml).read_text(encoding="utf-8")
        except OSError as e:
            die(f"Cannot read {args.from_yaml}: {e}", code=2)
        try:
            blocks = workout_dsl.parse_dsl(yaml_text)
        except ValueError as e:
            die(f"DSL error: {e}", code=2)
        structure = workout_dsl.to_tp_structure(blocks)
        est_if, est_tss = workout_dsl.estimate_tss_if(blocks)
        print(f"Estimated from structure: IF={est_if}, TSS={est_tss}")
    elif args.from_file:
        try:
            raw = Path(args.from_file).read_text(encoding="utf-8")
        except OSError as e:
            die(f"Cannot read {args.from_file}: {e}", code=2)
        try:
            structure = json.loads(raw)
        except json.JSONDecodeError as e:
            die(f"Invalid JSON in {args.from_file}: {e}", code=2)
        if not isinstance(structure, dict) or "structure" not in structure:
            die("Raw structure file must be a dict with a 'structure' key", code=2)

    # Warn if --auto-tss was passed but there's no DSL to estimate from
    if args.auto_tss and est_tss is None:
        print("Warning: --auto-tss has no effect without --from-yaml "
              "(no DSL blocks to estimate from)", file=sys.stderr)

    # Derive totalTimePlanned
    if structure is not None:
        total_s = 0
        for blk in structure.get("structure", []):
            total_s += int(blk.get("end", 0)) - int(blk.get("begin", 0))
        duration_h = total_s / 3600.0
        if args.duration is not None:
            print(f"Warning: --duration ignored when structure is provided "
                  f"(derived: {duration_h:.4f}h)", file=sys.stderr)
    else:
        if args.duration is None:
            die("--duration is required when no structure is provided", code=2)
        duration_h = float(args.duration)

    # Resolve TSS/IF: explicit wins, then --auto-tss, then null
    if args.tss is not None:
        tss_planned = float(args.tss)
    elif args.auto_tss and est_tss is not None:
        tss_planned = est_tss
    else:
        tss_planned = None

    if args.if_ is not None:
        if_planned = float(args.if_)
    elif args.auto_tss and est_if is not None:
        if_planned = est_if
    else:
        if_planned = None

    athlete_id = get_athlete_id()
    payload = {
        "athleteId": athlete_id,
        "workoutId": 0,
        "workoutDay": args.date,
        "title": args.title,
        "workoutTypeValueId": sport_id,
        "workoutSubTypeId": None,
        "code": None,
        "isHidden": None,
        "description": args.description,
        "userTags": "",
        "coachComments": None,
        "newComment": None,
        "publicSettingValue": 0,
        "sharedWorkoutInformationKey": None,
        "sharedWorkoutInformationExpireKey": None,
        "shortUrl": None,
        "distance": None,
        "distancePlanned": None,
        "distanceCustomized": None,
        "distanceUnitsCustomized": None,
        "totalTime": None,
        "totalTimePlanned": duration_h,
        "heartRateMinimum": None,
        "heartRateMaximum": None,
        "heartRateAverage": None,
        "hrAvg": None,
        "calories": None,
        "caloriesPlanned": None,
        "tssActual": None,
        "tssPlanned": tss_planned,
        "tssSource": None,
        "if": None,
        "ifPlanned": if_planned,
        "velocityAverage": None,
        "velocityAvg": None,
        "velocityPlanned": None,
        "velocityMaximum": None,
        "normalizedSpeedActual": None,
        "normalizedPowerActual": None,
        "powerAverage": None,
        "powerAvg": None,
        "powerMaximum": None,
        "energy": None,
        "energyPlanned": None,  # Do not infer energy from an assumed athlete FTP.
        "elevationGain": None,
        "elevationGainPlanned": None,
        "elevationLoss": None,
        "elevationMinimum": None,
        "elevationAverage": None,
        "elevationMaximum": None,
        "torqueAverage": None,
        "torqueMaximum": None,
        "tempMin": None,
        "tempAvg": None,
        "tempMax": None,
        "cadenceAverage": None,
        "cadenceAvg": None,
        "cadenceMaximum": None,
        "lastModifiedDate": None,
        "startTime": None,
        "startTimePlanned": None,
        "equipmentBikeId": None,
        "equipmentShoeId": None,
        "poolLengthOptionId": None,
        "isLocked": None,
        "complianceDurationPercent": None,
        "complianceDistancePercent": None,
        "complianceTssPercent": None,
        "orderOnDay": None,
        "personalRecordCount": 0,
        "structure": json.dumps(structure) if structure else None,
        "personalRecords": None,
        "feeling": None,
        "rpe": None,
        "syncedTo": None,
        "workoutComments": [],
    }

    if args.dry_run:
        print("Dry-run POST payload:")
        # Pretty-print with structure pre-parsed for readability
        preview = dict(payload)
        if isinstance(preview["structure"], str):
            preview["structure"] = json.loads(preview["structure"])
        print(json.dumps(preview, indent=2, ensure_ascii=False))
        return

    status, data = api_post(f"/fitness/v6/athletes/{athlete_id}/workouts", payload)
    if status < 200 or status >= 300:
        die(f"POST failed with HTTP {status}: {data}", code=3)

    # Extract new workoutId from the response
    new_id = None
    if isinstance(data, dict):
        new_id = data.get("workoutId")
    elif isinstance(data, list) and data and isinstance(data[0], dict):
        new_id = data[0].get("workoutId")
    if new_id is None:
        die("Create response missing workoutId", code=4)

    print(f"✓ created: workout {new_id} on {args.date} \"{args.title}\"")


def cmd_edit_raw(args: argparse.Namespace) -> None:
    """Replace the full workout object with the contents of a file (escape hatch)."""
    try:
        raw = Path(args.from_file).read_text(encoding="utf-8")
    except OSError as e:
        die(f"Cannot read {args.from_file}: {e}", code=2)
    try:
        proposed = json.loads(raw)
    except json.JSONDecodeError as e:
        die(f"Invalid JSON in {args.from_file}: {e}", code=2)
    if not isinstance(proposed, dict) or "workoutId" not in proposed:
        die("File must contain a workout object with a 'workoutId' field", code=2)

    def mutator(_current: dict) -> dict:
        # Ignore current; user provided a full replacement.
        return proposed

    mutate_workout(args.workout_id, mutator,
                   force=args.force, dry_run=args.dry_run,
                   verify_fields=["title", "tssPlanned", "ifPlanned",
                                  "totalTimePlanned", "workoutDay"])


# ─── CLI Setup ────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tp.py",
        description="TrainingPeaks CLI — access your training data from the command line.",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # auth
    p_auth = sub.add_parser("auth", help="Authenticate with a Production_tpAuth cookie")
    p_auth.add_argument("cookie", help="Value of the Production_tpAuth cookie")

    # auth-status
    sub.add_parser("auth-status", help="Check authentication status")

    # profile
    p_profile = sub.add_parser("profile", help="Get athlete profile")
    p_profile.add_argument("--json", action="store_true", help="Output raw JSON")

    # workouts
    p_workouts = sub.add_parser("workouts", help="List workouts in a date range")
    p_workouts.add_argument("start_date", help="Start date (YYYY-MM-DD)")
    p_workouts.add_argument("end_date", help="End date (YYYY-MM-DD)")
    p_workouts.add_argument("--filter", choices=["all", "planned", "completed"],
                            default="all", help="Filter workouts (default: all)")
    p_workouts.add_argument("--json", action="store_true", help="Output raw JSON")

    # workout
    p_workout = sub.add_parser("workout", help="Get full workout detail")
    p_workout.add_argument("workout_id", help="Workout ID")
    p_workout.add_argument("--json", action="store_true", help="Output raw JSON")

    # fitness
    p_fitness = sub.add_parser("fitness", help="Get CTL/ATL/TSB fitness data")
    p_fitness.add_argument("--days", type=int, default=90,
                           help="Days of history (default: 90, max: 365)")
    p_fitness.add_argument("--json", action="store_true", help="Output raw JSON")

    # peaks
    p_peaks = sub.add_parser("peaks", help="Get personal records")
    p_peaks.add_argument("sport", choices=["Bike", "Run"], help="Sport type")
    p_peaks.add_argument("pr_type", help="PR type (e.g., power20min, speed5K)")
    p_peaks.add_argument("--days", type=int, default=3650,
                         help="Days of history (default: 3650 ≈ 10 years)")
    p_peaks.add_argument("--json", action="store_true", help="Output raw JSON")

    # week-summary
    p_week = sub.add_parser("week-summary",
                             help="Print this week's TSS total + current CTL/ATL/TSB in one shot")
    p_week.add_argument("--json", action="store_true", help="Output raw JSON")

    # health-metrics
    p_health = sub.add_parser("health", help="Get consolidated health metrics (HRV, RHR, weight, etc.)")
    p_health.add_argument("start_date", help="Start date (YYYY-MM-DD)")
    p_health.add_argument("end_date", help="End date (YYYY-MM-DD)")
    p_health.add_argument("--json", action="store_true", help="Output raw JSON")

    # download-fit
    p_dl = sub.add_parser("download-fit", help="Download raw FIT file for a workout")
    p_dl.add_argument("workout_id", help="Workout ID")
    p_dl.add_argument("--file-id",
                      help="Optional raw file id from TrainingPeaks (rawfiledata endpoint); if omitted, the script will auto-discover it from workout details")
    p_dl.add_argument("--output-dir", help="Directory to save file (default: current directory)")
    p_dl.add_argument("--output-name", help="Output filename (default: <workoutId>-<fileId>.fit.gz)")

    # add-comment
    p_add_comment = sub.add_parser("add-comment", help="Add a comment to a workout")
    p_add_comment.add_argument("workout_id", help="Workout ID")
    p_add_comment.add_argument("comment", help="Comment text")

    # delete-comment
    p_del_comment = sub.add_parser("delete-comment", help="Delete a comment from a workout")
    p_del_comment.add_argument("workout_id", help="Workout ID")
    p_del_comment.add_argument("comment_id", help="Comment ID to delete")

    # edit-meta
    p_edit_meta = sub.add_parser("edit-meta", help="Edit metadata on a planned workout")
    p_edit_meta.add_argument("workout_id", help="Workout ID")
    p_edit_meta.add_argument("--title", help="Set workout title")
    p_edit_meta.add_argument("--description", help="Set workout description")
    p_edit_meta.add_argument("--tss", type=float, help="Set planned TSS")
    p_edit_meta.add_argument("--if", dest="if_", type=float, help="Set planned IF")
    p_edit_meta.add_argument("--duration", type=float,
                             help="Set planned total time in hours")
    p_edit_meta.add_argument("--energy", type=float, help="Set planned energy (kJ)")
    p_edit_meta.add_argument("--dry-run", action="store_true",
                             help="Print diff, don't PUT")
    p_edit_meta.add_argument("--force", action="store_true",
                             help="Allow editing completed workouts")

    # edit-structure
    p_edit_struct = sub.add_parser("edit-structure",
                                    help="Replace the interval structure of a planned workout")
    p_edit_struct.add_argument("workout_id", help="Workout ID")
    grp = p_edit_struct.add_mutually_exclusive_group(required=True)
    grp.add_argument("--from-yaml", dest="from_yaml",
                     help="Path to DSL YAML file")
    grp.add_argument("--from-file", dest="from_file",
                     help="Path to raw TP structure JSON file")
    p_edit_struct.add_argument("--auto-tss", action="store_true",
                                help="When using --from-yaml, also set tssPlanned/ifPlanned from the estimator")
    p_edit_struct.add_argument("--dry-run", action="store_true",
                                help="Print diff, don't PUT")
    p_edit_struct.add_argument("--force", action="store_true",
                                help="Allow editing completed workouts")

    # edit-date
    p_edit_date = sub.add_parser("edit-date",
                                  help="Move a planned workout to a different date")
    p_edit_date.add_argument("workout_id", help="Workout ID")
    p_edit_date.add_argument("--to", required=True,
                              help="Target date (YYYY-MM-DD)")
    p_edit_date.add_argument("--dry-run", action="store_true",
                              help="Print diff, don't PUT")
    p_edit_date.add_argument("--force", action="store_true",
                              help="Allow editing completed workouts")

    # delete
    p_delete = sub.add_parser("delete", help="Delete a planned workout")
    p_delete.add_argument("workout_id", help="Workout ID")
    p_delete.add_argument("--yes", action="store_true",
                           help="Skip interactive confirmation")
    p_delete.add_argument("--force", action="store_true",
                           help="Allow deleting completed workouts")

    # edit (full-PUT escape hatch)
    p_edit = sub.add_parser("edit",
                             help="Replace a workout from a raw TP workout JSON file")
    p_edit.add_argument("workout_id", help="Workout ID")
    p_edit.add_argument("--from-file", dest="from_file", required=True,
                         help="Path to raw TP workout JSON file")
    p_edit.add_argument("--dry-run", action="store_true",
                         help="Print diff, don't PUT")
    p_edit.add_argument("--force", action="store_true",
                         help="Allow editing completed workouts")

    # create
    p_create = sub.add_parser("create", help="Create a new planned workout")
    p_create.add_argument("--date", required=True, help="Workout date (YYYY-MM-DD)")
    p_create.add_argument("--title", required=True, help="Workout title")
    p_create.add_argument("--sport", choices=list(_SPORT_IDS.keys()),
                          help="Sport name (default: bike)")
    p_create.add_argument("--type-id", dest="type_id", type=int,
                          help="Raw workoutTypeValueId (overrides --sport)")
    p_create.add_argument("--from-yaml", dest="from_yaml",
                          help="Path to DSL YAML file")
    p_create.add_argument("--from-file", dest="from_file",
                          help="Path to raw TP structure JSON file")
    p_create.add_argument("--duration", type=float,
                          help="Planned total time in hours (required if no structure)")
    p_create.add_argument("--tss", type=float, help="Planned TSS")
    p_create.add_argument("--if", dest="if_", type=float, help="Planned IF")
    p_create.add_argument("--auto-tss", dest="auto_tss", action="store_true",
                          help="With --from-yaml, derive TSS/IF from structure")
    p_create.add_argument("--description", help="Optional workout description")
    p_create.add_argument("--dry-run", dest="dry_run", action="store_true",
                          help="Print POST payload, don't send")

    # sync-fit
    p_sync = sub.add_parser("sync-fit", help="Sync recent completed workouts' FIT files into a local directory")
    p_sync.add_argument("--days", type=int, default=7,
                        help="How many days back to sync (default: 7)")
    p_sync.add_argument("--output-dir", help="Directory to save files (default: fit_exports)")
    p_sync.add_argument("--sport", choices=["Bike", "Run", "All"], default="Bike",
                        help="Sport filter (default: Bike)")
    p_sync.add_argument("--once", action="store_true",
                        help="Run a single sync now and ignore random scheduling state")
    p_sync.add_argument("--min-interval-min", type=int, default=1,
                        help="Minimum minutes between syncs when used with periodic triggers (default: 1)")
    p_sync.add_argument("--max-interval-min", type=int, default=10,
                        help="Maximum minutes between syncs when used with periodic triggers (default: 10)")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "auth": cmd_auth,
        "auth-status": cmd_auth_status,
        "profile": cmd_profile,
        "workouts": cmd_workouts,
        "workout": cmd_workout,
        "fitness": cmd_fitness,
        "week-summary": cmd_week_summary,
        "peaks": cmd_peaks,
        "health": cmd_health,
        "download-fit": cmd_download_fit,
        "sync-fit": cmd_sync_fit,
        "add-comment": cmd_add_comment,
        "delete-comment": cmd_delete_comment,
        "edit-meta": cmd_edit_meta,
        "edit-structure": cmd_edit_structure,
        "edit-date": cmd_edit_date,
        "delete": cmd_delete,
        "edit": cmd_edit_raw,
        "create": cmd_create,
    }

    fn = commands.get(args.command)
    if fn:
        fn(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
