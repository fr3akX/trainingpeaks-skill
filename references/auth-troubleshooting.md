# TrainingPeaks Auth Troubleshooting

## Quick Diagnosis Flowchart

```
tp.py auth fails
  ├─ HTTP 401 → cookie expired/invalid → ask user for fresh cookie
  ├─ HTTP 500 → TP server outage → manual store + auto-retry
  └─ HTTP 403/404 → endpoint issue → check TP_API_BASE / TOKEN_ENDPOINT in tp.py
```

## Verifying HTTP 500 is Server-Side (Not Cookie)

When `tp.py auth` returns `Token exchange failed (HTTP 500)`, confirm it's a TP
server outage and not a cookie problem:

```bash
# Direct curl to the token endpoint — shows the raw response body
curl -s -w "\nHTTP_STATUS:%{http_code}\n" \
  -H "Cookie: Production_tpAuth=<cookie_value>" \
  -H "Accept: application/json" \
  "https://tpapi.trainingpeaks.com/users/v3/token"
```

**500 + `{"message":"An error has occurred."}`** = server-side outage. The
endpoint exists (v1/v2 return 404, v3 returns 500), and the cookie format is
correct (starts with `V001`, typically 1700–1800 chars). Expired cookies get
401, not 500.

## Manual Cookie Storage

When the token exchange is down but the cookie is valid, store it manually so
the next successful exchange picks it up automatically:

```python
import os, stat
cookie = "<paste_cookie_value>"
path = os.path.expanduser("~/.trainingpeaks/cookie")
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w") as f:
    f.write(cookie.strip())
os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
print(f"✓ Cookie stored to {path} ({len(cookie)} chars)")
```

After storing, verify with `tp.py auth-status` — it should show "Cookie: stored
(file)" and "Token: expired (will auto-refresh)". The next `tp.py` read command
will attempt the token exchange via `ensure_token()`.

## Checking for Expired Cached Tokens

The cached token in `~/.trainingpeaks/token.json` has an `expires_at` timestamp.
When it's expired and the server is down, every API call will fail at the token
refresh step. Check with:

```python
import json, time
d = json.loads(open(os.path.expanduser("~/.trainingpeaks/token.json")).read())
exp = d["expires_at"]
remaining = exp - time.time()
print(f"Status: {'VALID' if remaining > 0 else 'EXPIRED'} ({remaining:.0f}s)")
```

## Auto-Retry Cron Pattern

For TP server outages, create a cron job that retries auth periodically and
only alerts on success:

```
Schedule: every 15 minutes, repeat ~12 times (3 hours)
Toolsets: terminal only
Prompt: |
  Run: tp.py auth-status && tp.py fitness --days 1
  If success → report recovery + current CTL/ATL/TSB
  If HTTP 500 → reply HEARTBEAT_OK (silent — no alert on ongoing outage)
```

This avoids spamding the user about an ongoing outage while ensuring they're
notified the moment TP comes back online.
