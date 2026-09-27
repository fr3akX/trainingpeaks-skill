"""Unit tests for workout_dsl.py (pure-function DSL translator)."""
import unittest

from workout_dsl import Step, RepBlock, parse_duration, parse_ftp
from workout_dsl import parse_step
from workout_dsl import parse_dsl
from workout_dsl import to_tp_structure
from workout_dsl import estimate_tss_if


class TestParseDuration(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(parse_duration("90s"), 90)

    def test_minutes(self):
        self.assertEqual(parse_duration("10min"), 600)

    def test_hours(self):
        self.assertEqual(parse_duration("2h"), 7200)

    def test_compound(self):
        self.assertEqual(parse_duration("1h30min"), 5400)

    def test_invalid_format(self):
        with self.assertRaises(ValueError) as ctx:
            parse_duration("10 minutes")
        self.assertIn("duration", str(ctx.exception).lower())

    def test_zero_or_negative(self):
        with self.assertRaises(ValueError):
            parse_duration("0s")
        with self.assertRaises(ValueError):
            parse_duration("-5min")


class TestParseFtp(unittest.TestCase):
    def test_single_value(self):
        self.assertEqual(parse_ftp(70), (70, 70))
        self.assertEqual(parse_ftp("70"), (70, 70))

    def test_range(self):
        self.assertEqual(parse_ftp("40-50"), (40, 50))
        self.assertEqual(parse_ftp("106-121"), (106, 121))

    def test_inverted_range_rejected(self):
        with self.assertRaises(ValueError):
            parse_ftp("50-40")

    def test_out_of_bounds_rejected(self):
        with self.assertRaises(ValueError):
            parse_ftp("0-10")      # below 1
        with self.assertRaises(ValueError):
            parse_ftp("200-250")   # above 200% upper cap


class TestDataclasses(unittest.TestCase):
    def test_step_construction(self):
        s = Step(type="active", duration_s=600, ftp_min=70, ftp_max=80, name="Tempo")
        self.assertEqual(s.duration_s, 600)
        self.assertEqual(s.ftp_max, 80)

    def test_repblock_construction(self):
        inner = [Step(type="active", duration_s=20, ftp_min=106, ftp_max=121, name="Hard")]
        r = RepBlock(count=3, steps=inner)
        self.assertEqual(r.count, 3)
        self.assertEqual(len(r.steps), 1)


class TestParseStep(unittest.TestCase):
    def test_valid_step_with_all_fields(self):
        s = parse_step(
            {"type": "active", "duration": "5min", "ftp": "70-80", "name": "Tempo"},
            step_index=0,
        )
        self.assertEqual(s.type, "active")
        self.assertEqual(s.duration_s, 300)
        self.assertEqual(s.ftp_min, 70)
        self.assertEqual(s.ftp_max, 80)
        self.assertEqual(s.name, "Tempo")

    def test_default_name_warmup(self):
        s = parse_step({"type": "warmup", "duration": "10min", "ftp": "40-50"}, 0)
        self.assertEqual(s.name, "Warm up")

    def test_default_name_active(self):
        s = parse_step({"type": "active", "duration": "5min", "ftp": 70}, 0)
        self.assertEqual(s.name, "Active")

    def test_default_name_rest(self):
        s = parse_step({"type": "rest", "duration": "2min", "ftp": "50-60"}, 0)
        self.assertEqual(s.name, "Recovery")

    def test_default_name_cooldown(self):
        s = parse_step({"type": "cooldown", "duration": "10min", "ftp": "40-50"}, 0)
        self.assertEqual(s.name, "Cool down")

    def test_unknown_type(self):
        with self.assertRaises(ValueError) as ctx:
            parse_step({"type": "burst", "duration": "30s", "ftp": "90-100"}, step_index=2)
        msg = str(ctx.exception)
        self.assertIn("Step 2", msg)
        self.assertIn("burst", msg)

    def test_missing_duration(self):
        with self.assertRaises(ValueError) as ctx:
            parse_step({"type": "active", "ftp": "70-80"}, step_index=1)
        self.assertIn("Step 1", str(ctx.exception))
        self.assertIn("duration", str(ctx.exception))

    def test_missing_ftp(self):
        with self.assertRaises(ValueError) as ctx:
            parse_step({"type": "active", "duration": "5min"}, step_index=3)
        self.assertIn("Step 3", str(ctx.exception))
        self.assertIn("ftp", str(ctx.exception))


EXAMPLE_INTERVALS_YAML = """\
steps:
  - { type: warmup,  duration: 10min, ftp: 40-50 }
  - { type: active,  duration: 5min,  ftp: 70-80, name: "Tempo bridge" }
  - { type: active,  duration: 6min,  ftp: 90-95, name: "Interval A" }
  - { type: rest,    duration: 4min,  ftp: 50-60 }
  - { type: active,  duration: 4min,  ftp: 90-94, name: "Interval B" }
  - { type: active,  duration: 1min,  ftp: 105-110, name: "Short effort" }
  - { type: rest,    duration: 4min,  ftp: 50-60 }
  - repeat: 3
    steps:
      - { type: active, duration: 20s,  ftp: 106-121, name: "Hard" }
      - { type: rest,   duration: 1min, ftp: 50-60,   name: "Easy" }
  - { type: cooldown, duration: 10min, ftp: 40-50 }
"""


class TestParseDsl(unittest.TestCase):
    def test_example_intervals_structure(self):
        blocks = parse_dsl(EXAMPLE_INTERVALS_YAML)
        # 9 top-level entries: 7 leading leaves + 1 rep block + 1 cooldown
        self.assertEqual(len(blocks), 9)
        # leaves
        for i in (0, 1, 2, 3, 4, 5, 6, 8):
            self.assertIsInstance(blocks[i], Step)
        # rep block
        self.assertIsInstance(blocks[7], RepBlock)
        self.assertEqual(blocks[7].count, 3)
        self.assertEqual(len(blocks[7].steps), 2)
        self.assertEqual(blocks[7].steps[0].name, "Hard")
        self.assertEqual(blocks[7].steps[1].name, "Easy")

    def test_missing_steps_key(self):
        with self.assertRaises(ValueError) as ctx:
            parse_dsl("unrelated: true\n")
        self.assertIn("steps", str(ctx.exception))

    def test_empty_steps_rejected(self):
        with self.assertRaises(ValueError):
            parse_dsl("steps: []\n")

    def test_rep_count_below_two_rejected(self):
        yaml_text = (
            "steps:\n"
            "  - repeat: 1\n"
            "    steps:\n"
            "      - { type: active, duration: 1min, ftp: 80 }\n"
        )
        with self.assertRaises(ValueError) as ctx:
            parse_dsl(yaml_text)
        self.assertIn("repeat", str(ctx.exception).lower())

    def test_nested_rep_rejected(self):
        yaml_text = (
            "steps:\n"
            "  - repeat: 2\n"
            "    steps:\n"
            "      - repeat: 3\n"
            "        steps:\n"
            "          - { type: active, duration: 20s, ftp: 100 }\n"
        )
        with self.assertRaises(ValueError) as ctx:
            parse_dsl(yaml_text)
        self.assertIn("nested", str(ctx.exception).lower())

    def test_invalid_yaml_raises_valueerror(self):
        # Malformed YAML: unterminated flow mapping
        with self.assertRaises(ValueError) as ctx:
            parse_dsl("steps: [ {type: active, duration: 5min")
        # The wrapped error should mention YAML
        self.assertIn("yaml", str(ctx.exception).lower())

    def test_non_string_input_raises_typeerror(self):
        with self.assertRaises(TypeError):
            parse_dsl(None)
        with self.assertRaises(TypeError):
            parse_dsl(123)


class TestToTpStructure(unittest.TestCase):
    def test_single_step_leaf(self):
        blocks = [Step(type="warmup", duration_s=600, ftp_min=40, ftp_max=50, name="Warm up")]
        out = to_tp_structure(blocks)
        self.assertEqual(out["primaryLengthMetric"], "duration")
        self.assertEqual(out["primaryIntensityMetric"], "percentOfFtp")
        struct = out["structure"]
        self.assertEqual(len(struct), 1)
        blk = struct[0]
        self.assertEqual(blk["type"], "step")
        self.assertEqual(blk["begin"], 0)
        self.assertEqual(blk["end"], 600)
        self.assertEqual(blk["length"], {"value": 1, "unit": "repetition"})
        inner = blk["steps"][0]
        self.assertEqual(inner["name"], "Warm up")
        self.assertEqual(inner["length"], {"value": 600, "unit": "second"})
        self.assertEqual(inner["targets"], [{"minValue": 40, "maxValue": 50}])
        self.assertEqual(inner["intensityClass"], "warmUp")
        self.assertFalse(inner["openDuration"])

    def test_intensity_class_mapping(self):
        cases = [
            ("warmup", "warmUp"),
            ("active", "active"),
            ("rest", "rest"),
            ("cooldown", "coolDown"),
        ]
        for dsl_type, tp_class in cases:
            out = to_tp_structure([Step(type=dsl_type, duration_s=60,
                                        ftp_min=50, ftp_max=60, name="X")])
            self.assertEqual(out["structure"][0]["steps"][0]["intensityClass"], tp_class)

    def test_rep_block_cumulative_offsets(self):
        blocks = [
            Step(type="warmup", duration_s=600, ftp_min=40, ftp_max=50, name="Warm up"),
            RepBlock(count=3, steps=[
                Step(type="active", duration_s=20, ftp_min=106, ftp_max=121, name="Hard"),
                Step(type="rest",   duration_s=60, ftp_min=50,  ftp_max=60,  name="Easy"),
            ]),
            Step(type="cooldown", duration_s=300, ftp_min=40, ftp_max=50, name="Cool down"),
        ]
        out = to_tp_structure(blocks)
        struct = out["structure"]
        self.assertEqual(len(struct), 3)
        self.assertEqual(struct[0]["begin"], 0)
        self.assertEqual(struct[0]["end"], 600)
        # rep block: 3 × (20+60) = 240s
        self.assertEqual(struct[1]["begin"], 600)
        self.assertEqual(struct[1]["end"], 840)
        self.assertEqual(struct[1]["length"], {"value": 3, "unit": "repetition"})
        self.assertEqual(len(struct[1]["steps"]), 2)
        # cooldown follows
        self.assertEqual(struct[2]["begin"], 840)
        self.assertEqual(struct[2]["end"], 1140)

    def test_offsets_monotone_nondecreasing(self):
        blocks = [
            Step(type="warmup",   duration_s=600, ftp_min=40, ftp_max=50, name="WU"),
            Step(type="active",   duration_s=300, ftp_min=70, ftp_max=80, name="Tempo"),
            RepBlock(count=2, steps=[
                Step(type="active", duration_s=60, ftp_min=100, ftp_max=110, name="On"),
                Step(type="rest",   duration_s=60, ftp_min=50,  ftp_max=60,  name="Off"),
            ]),
            Step(type="cooldown", duration_s=600, ftp_min=40, ftp_max=50, name="CD"),
        ]
        struct = to_tp_structure(blocks)["structure"]
        prev = 0
        for blk in struct:
            self.assertGreaterEqual(blk["begin"], prev)
            self.assertGreater(blk["end"], blk["begin"])
            prev = blk["end"]

    def test_polyline_present_and_bounded(self):
        blocks = [
            Step(type="warmup",   duration_s=600, ftp_min=40, ftp_max=50, name="WU"),
            Step(type="active",   duration_s=300, ftp_min=90, ftp_max=95, name="Active"),
            Step(type="cooldown", duration_s=600, ftp_min=40, ftp_max=50, name="CD"),
        ]
        out = to_tp_structure(blocks)
        poly = out["polyline"]
        self.assertIsInstance(poly, list)
        self.assertGreater(len(poly), 0)
        for x, y in poly:
            self.assertGreaterEqual(x, 0.0)
            self.assertLessEqual(x, 1.0)
            self.assertGreaterEqual(y, 0.0)
        # starts at x=0, ends at x=1
        self.assertEqual(poly[0][0], 0)
        self.assertAlmostEqual(poly[-1][0], 1.0, places=6)

    def test_empty_blocks_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            to_tp_structure([])
        self.assertIn("empty", str(ctx.exception).lower())

    def test_unknown_step_type_raises_valueerror(self):
        # Construct a Step directly with a type not in _INTENSITY_CLASS.
        # This bypasses parse_step which would have rejected it.
        bad = Step(type="sprint", duration_s=60, ftp_min=90, ftp_max=95, name="X")
        with self.assertRaises(ValueError) as ctx:
            to_tp_structure([bad])
        self.assertIn("sprint", str(ctx.exception))

    def test_primary_intensity_target_or_range(self):
        blocks = [Step(type="active", duration_s=60, ftp_min=70, ftp_max=80, name="X")]
        out = to_tp_structure(blocks)
        self.assertEqual(out["primaryIntensityTargetOrRange"], "range")


class TestEstimateTssIf(unittest.TestCase):
    def test_pure_ftp_step(self):
        # 1 hour @ 100% FTP → IF 1.0, TSS 100
        blocks = [Step(type="active", duration_s=3600, ftp_min=100, ftp_max=100, name="FTP")]
        if_, tss = estimate_tss_if(blocks)
        self.assertAlmostEqual(if_, 1.0, places=3)
        self.assertAlmostEqual(tss, 100.0, places=1)

    def test_range_uses_midpoint(self):
        # 1 hour @ 70-80% FTP → midpoint 75% → IF 0.75, TSS 56.25
        blocks = [Step(type="active", duration_s=3600, ftp_min=70, ftp_max=80, name="Z3")]
        if_, tss = estimate_tss_if(blocks)
        self.assertAlmostEqual(if_, 0.75, places=3)
        self.assertAlmostEqual(tss, 56.25, places=2)

    def test_time_weighted_across_steps(self):
        # 30 min @ 50% + 30 min @ 90%.
        # Fourth-power weighting raises modelled NP/IF above the 70% arithmetic mean.
        blocks = [
            Step(type="active", duration_s=1800, ftp_min=50, ftp_max=50, name="A"),
            Step(type="active", duration_s=1800, ftp_min=90, ftp_max=90, name="B"),
        ]
        if_, tss = estimate_tss_if(blocks)
        self.assertAlmostEqual(if_, 0.7739, places=4)
        self.assertAlmostEqual(tss, 59.89, places=2)

    def test_rep_block_counts_each_repetition(self):
        # 3 × (60s @ 100% + 60s @ 50%) = 360s, avg 75%
        # The modelled NP uses 30-second rolling power and must exceed 75%.
        blocks = [RepBlock(count=3, steps=[
            Step(type="active", duration_s=60, ftp_min=100, ftp_max=100, name="On"),
            Step(type="rest",   duration_s=60, ftp_min=50,  ftp_max=50,  name="Off"),
        ])]
        if_, tss = estimate_tss_if(blocks)
        self.assertAlmostEqual(if_, 0.8263, places=4)
        self.assertAlmostEqual(tss, 6.83, places=2)

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            estimate_tss_if([])


if __name__ == "__main__":
    unittest.main()
