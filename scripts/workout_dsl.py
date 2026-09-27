"""Pure-function DSL translator for TrainingPeaks workout structures.

Parses a YAML description of a workout (a list of steps / repetition blocks
with per-step duration and target FTP percentages) and translates it into
TrainingPeaks' native nested structure dict that the PUT endpoint accepts.

No I/O, no HTTP, no auth. Consumers: tp.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Union


@dataclass
class Step:
    type: str              # 'warmup' | 'active' | 'rest' | 'cooldown'
    duration_s: int        # seconds, > 0
    ftp_min: int           # percent, 1..200
    ftp_max: int           # percent, 1..200, >= ftp_min
    name: str              # display name


@dataclass
class RepBlock:
    count: int             # >= 2
    steps: list[Step] = field(default_factory=list)


Block = Union[Step, RepBlock]


# ─── Primitive parsers ────────────────────────────────────────────────────────

_DURATION_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)min)?(?:(\d+)s)?$")


def parse_duration(value: str) -> int:
    """Parse '10min', '90s', '2h', '1h30min' → seconds.

    Rejects zero/negative durations, whitespace, unknown units.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"duration must be a non-empty string, got {value!r}")
    m = _DURATION_RE.match(value.strip())
    if not m or not any(m.groups()):
        raise ValueError(
            f"duration {value!r} must look like '10min', '90s', '2h', or '1h30min'"
        )
    h, mn, sec = (int(g) if g else 0 for g in m.groups())
    total = h * 3600 + mn * 60 + sec
    if total <= 0:
        raise ValueError(f"duration must be > 0, got {value!r}")
    return total


def parse_ftp(value) -> tuple[int, int]:
    """Parse 70 / '70' / '40-50' → (min, max) percent of FTP.

    Validates 1 <= min <= max <= 200.
    """
    if isinstance(value, int):
        s = str(value)
    elif isinstance(value, str):
        s = value.strip()
    else:
        raise ValueError(f"ftp must be int or str, got {type(value).__name__}")

    if "-" in s:
        a, b = s.split("-", 1)
        try:
            lo, hi = int(a), int(b)
        except ValueError as e:
            raise ValueError(f"ftp range {value!r} must be 'N-M' integers") from e
    else:
        try:
            lo = hi = int(s)
        except ValueError as e:
            raise ValueError(f"ftp {value!r} must be an integer") from e

    if lo < 1 or hi > 200 or lo > hi:
        raise ValueError(
            f"ftp {value!r} out of bounds: need 1 <= min <= max <= 200"
        )
    return lo, hi


_VALID_TYPES = ("warmup", "active", "rest", "cooldown")

_DEFAULT_NAMES = {
    "warmup":   "Warm up",
    "active":   "Active",
    "rest":     "Recovery",
    "cooldown": "Cool down",
}


def parse_step(raw: dict, step_index: int) -> Step:
    """Parse one DSL step dict into a Step.

    Errors include the step index (1-based in the user's file would be better,
    but we pass 0-based here; the caller can format as needed).
    """
    loc = f"Step {step_index}"

    type_ = raw.get("type")
    if type_ not in _VALID_TYPES:
        raise ValueError(
            f"{loc}: type {type_!r} not recognized. "
            f"Valid: {', '.join(_VALID_TYPES)}"
        )

    if "duration" not in raw:
        raise ValueError(f"{loc}: 'duration' missing")
    duration_s = parse_duration(str(raw["duration"]))

    if "ftp" not in raw:
        raise ValueError(f"{loc}: 'ftp' missing")
    ftp_min, ftp_max = parse_ftp(raw["ftp"])

    name = raw.get("name") or _DEFAULT_NAMES[type_]
    return Step(type=type_, duration_s=duration_s, ftp_min=ftp_min,
                ftp_max=ftp_max, name=name)


def parse_dsl(yaml_text: str) -> list[Block]:
    """Parse a full DSL document into a list of blocks.

    Each top-level entry is either a Step (dict with 'type') or a RepBlock
    (dict with 'repeat' and 'steps'). Nested repetitions are rejected.
    """
    if not isinstance(yaml_text, str):
        raise TypeError(f"yaml_text must be str, got {type(yaml_text).__name__}")

    try:
        import yaml  # PyYAML
    except ImportError as e:
        raise ImportError("PyYAML is required: pip install pyyaml") from e

    try:
        doc = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"YAML syntax error: {e}") from e
    if not isinstance(doc, dict) or "steps" not in doc:
        raise ValueError("DSL root must be a mapping with a 'steps' key")

    raw_entries = doc["steps"]
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError("'steps' must be a non-empty list")

    blocks: list[Block] = []
    for idx, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            raise ValueError(f"Step {idx}: expected a mapping, got {type(entry).__name__}")

        if "repeat" in entry:
            count = entry["repeat"]
            if not isinstance(count, int) or count < 2:
                raise ValueError(f"Step {idx}: 'repeat' must be an integer >= 2")

            inner_raw = entry.get("steps")
            if not isinstance(inner_raw, list) or not inner_raw:
                raise ValueError(f"Step {idx}: repetition block missing non-empty 'steps'")

            inner_steps: list[Step] = []
            for j, inner in enumerate(inner_raw):
                if not isinstance(inner, dict):
                    raise ValueError(f"Step {idx}.{j}: expected a mapping")
                if "repeat" in inner:
                    raise ValueError(f"Step {idx}.{j}: nested repetitions are not supported")
                inner_steps.append(parse_step(inner, step_index=idx))
            blocks.append(RepBlock(count=count, steps=inner_steps))
        else:
            blocks.append(parse_step(entry, step_index=idx))

    return blocks


_INTENSITY_CLASS = {
    "warmup":   "warmUp",
    "active":   "active",
    "rest":     "rest",
    "cooldown": "coolDown",
}


def _render_step_inner(step: Step) -> dict:
    """Render a Step into the inner TP step dict (without begin/end)."""
    intensity_class = _INTENSITY_CLASS.get(step.type)
    if intensity_class is None:
        raise ValueError(
            f"unknown step type {step.type!r}; expected one of {list(_INTENSITY_CLASS)}"
        )
    return {
        "name": step.name,
        "length": {"value": step.duration_s, "unit": "second"},
        "targets": [{"minValue": step.ftp_min, "maxValue": step.ftp_max}],
        "intensityClass": intensity_class,
        "openDuration": False,
    }


def _block_duration(blk: Block) -> int:
    if isinstance(blk, Step):
        return blk.duration_s
    # RepBlock: count × sum(inner durations)
    return blk.count * sum(s.duration_s for s in blk.steps)


def _build_polyline(blocks: list[Block]) -> list[list[float]]:
    """Simple polyline: vertices at each leaf step boundary, y = ftp_max/100.

    Produces a step-wise profile: [0,0]→[0,y1]→[x1,y1]→[x1,0]→[x1,y2]→...→[1,0].
    Good enough for TP to render a silhouette; exact vertical shape is
    cosmetic, the structure data is what drives workout execution.
    """
    # Flatten: (start_time_s, end_time_s, ftp_max)
    segments: list[tuple[int, int, int]] = []
    t = 0
    for blk in blocks:
        if isinstance(blk, Step):
            segments.append((t, t + blk.duration_s, blk.ftp_max))
            t += blk.duration_s
        else:
            for _ in range(blk.count):
                for s in blk.steps:
                    segments.append((t, t + s.duration_s, s.ftp_max))
                    t += s.duration_s
    total = t if t > 0 else 1
    poly: list[list[float]] = [[0, 0]]
    for start, end, ftp_max in segments:
        x0 = start / total
        x1 = end / total
        y = ftp_max / 100.0
        poly.append([round(x0, 6), y])
        poly.append([round(x1, 6), y])
        poly.append([round(x1, 6), 0])
    return poly


def to_tp_structure(blocks: list[Block]) -> dict:
    """Translate parsed blocks into TP's native `structure` dict.

    Output has keys: primaryLengthMetric, primaryIntensityMetric,
    primaryIntensityTargetOrRange, structure, polyline.
    """
    if not blocks:
        raise ValueError("cannot translate empty block list")

    out_blocks: list[dict] = []
    t = 0
    for blk in blocks:
        if isinstance(blk, Step):
            out_blocks.append({
                "type": "step",
                "length": {"value": 1, "unit": "repetition"},
                "steps": [_render_step_inner(blk)],
                "begin": t,
                "end": t + blk.duration_s,
            })
            t += blk.duration_s
        else:  # RepBlock
            rep_dur = _block_duration(blk)
            out_blocks.append({
                "type": "repetition",
                "length": {"value": blk.count, "unit": "repetition"},
                "steps": [_render_step_inner(s) for s in blk.steps],
                "begin": t,
                "end": t + rep_dur,
            })
            t += rep_dur

    return {
        "primaryLengthMetric": "duration",
        "primaryIntensityMetric": "percentOfFtp",
        "primaryIntensityTargetOrRange": "range",
        "structure": out_blocks,
        "polyline": _build_polyline(blocks),
    }


def _step_midpoint_fraction(step: Step) -> float:
    """Midpoint of the step's FTP range as a 0..2 fraction (100% = 1.0)."""
    return (step.ftp_min + step.ftp_max) / 200.0


def _expand_midpoint_power_fractions(blocks: list[Block]) -> list[float]:
    """Expand the planned structure into one midpoint power fraction per second."""
    power: list[float] = []
    for blk in blocks:
        steps = [blk] if isinstance(blk, Step) else blk.steps
        repeats = 1 if isinstance(blk, Step) else blk.count
        for _ in range(repeats):
            for step in steps:
                power.extend([_step_midpoint_fraction(step)] * step.duration_s)
    return power


def _modelled_np_fraction(power: list[float], window_s: int = 30) -> float:
    """Calculate modelled normalized power from a 1 Hz FTP-fraction trace.

    Uses the standard 30-second rolling-average, fourth-power NP method.
    A workout shorter than the rolling window falls back to its mean power,
    because no full 30-second window exists.
    """
    if not power:
        raise ValueError("cannot estimate from empty power trace")
    if len(power) < window_s:
        return sum(power) / len(power)

    rolling_sum = sum(power[:window_s])
    fourth_sum = (rolling_sum / window_s) ** 4
    count = 1
    for index in range(window_s, len(power)):
        rolling_sum += power[index] - power[index - window_s]
        fourth_sum += (rolling_sum / window_s) ** 4
        count += 1
    return (fourth_sum / count) ** 0.25


def estimate_tss_if(blocks: list[Block]) -> tuple[float, float]:
    """Estimate IF and TSS from modelled normalized power.

    Every FTP-targeted step is represented at its target-range midpoint, then
    expanded to a one-second power trace. IF is the modelled normalized-power
    fraction and TSS uses the standard Coggan formula:
    ``duration_hours × IF² × 100``.
    """
    if not blocks:
        raise ValueError("cannot estimate from empty block list")

    power = _expand_midpoint_power_fractions(blocks)
    total_s = len(power)
    if_ = _modelled_np_fraction(power)
    duration_h = total_s / 3600.0
    tss = duration_h * (if_ ** 2) * 100.0
    return round(if_, 4), round(tss, 2)
