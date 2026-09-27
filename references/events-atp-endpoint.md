# TrainingPeaks Events / ATP API

## Endpoint

The TP internal API exposes athlete events (races, ATP priority, race type) via the
fitness v6 endpoint with dates in the path (same pattern as workouts):

```
GET /fitness/v6/athletes/{athleteId}/events/{startDate}/{endDate}
```

Example:
```bash
python3 -c "
import sys
sys.path.insert(0, 'scripts')  # Run from the skill directory.
from tp import api_get, get_athlete_id
athlete_id = get_athlete_id()
status, data = api_get(f'/fitness/v6/athletes/{athlete_id}/events/2026-01-01/2026-12-31')
if status != 200 or not isinstance(data, list):
    raise RuntimeError(f'Events request failed: HTTP {status}')
for ev in sorted(data, key=lambda x: x['eventDate']):
    print(f\"{ev['eventDate'][:10]}  {ev['atpPriority']:<4}  {ev['eventType']:<20} {ev['raceTypeDuration']:<20} {ev['name']}\")
"
```

## Event Object Fields

| Field | Type | Notes |
|---|---|---|
| `id` | int | Event ID |
| `personId` | int | Athlete ID |
| `eventDate` | string | ISO date (`2026-07-26T00:00:00`) |
| `name` | string | Event name |
| `eventType` | string | `CyclingMountain`, `CyclingRoad`, `CyclingOther`, `CyclingCyclocross`, etc. |
| `raceTypeDuration` | string | `MtbOverTwoHours`, `MtbUnderTwoHours`, `RoadBikeOverTwoHours`, etc. |
| `atpPriority` | string | **`A`**, **`B`**, or **`C`** — race priority in the ATP |
| `atpId` | int | Links to the annual training plan |
| `atpWeekId` | int | Links to a specific ATP week |
| `ctlTarget` | float/null | Target CTL for the event (often null) |
| `description` | string/null | Optional description |
| `goals` | object | Target goals (distance, time, place, PR) — usually empty |
| `legs` | array | Multi-day event legs (usually empty) |
| `workouts` | array | Linked workout IDs (usually empty) |
| `isHidden` | bool | Hidden from calendar |
| `isLocked` | bool | Locked from editing |

## Priority Interpretation

- **A** — Peak/primary target. Full taper, peak freshness.
- **B** — Secondary target. Light taper, race-fit.
- **C** — Training race / low priority. No taper, train through.

## Discovery Notes

This endpoint was NOT documented anywhere in the skill or tp.py CLI. Discovered
by probing the fitness v6 API path pattern. Key findings during discovery:

- `/fitness/v6/athletes/{id}/events` (no dates) → HTTP 405 (Method Not Allowed for GET)
- `/fitness/v6/athletes/{id}/events/{start}/{end}` → **200 OK** (date-in-path pattern)
- Other tried-and-failed patterns: `/races/`, `/atp/`, `/annualTrainingPlan/`, `/events/v1/`, `/athlete/v1/{id}/events`, `/mobileIntegration/`, `/webIntegration/` — all 404

The CLI (`tp.py`) does not have a command for events yet. Access via `api_get()`
from Python until a CLI command is added.

## Race Type Duration Values Seen

| Value | Meaning |
|---|---|
| `MtbOverTwoHours` | MTB marathon (>2h) |
| `MtbUnderTwoHours` | XCO / short track (<2h) |
| `RoadBikeOverTwoHours` | Road/gravel endurance (>2h) |
| `RoadBikeUnderTwoHours` | Road crit / short race (<2h) |

## Use Case

When the athlete references upcoming races, nationals, or asks about scheduling
("when's the next A race?"), pull the events endpoint instead of searching
workouts. Events carry ATP priority and race type, which workouts don't.
