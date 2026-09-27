# Planned TSS and IF

## Select the intended workflow

- For a structured YAML workout, `--auto-tss` sets planned TSS/IF using the modelled normalized-power estimator. This is a local estimate, not TrainingPeaks' own calculation or measured load.
- On creation, omitting `--auto-tss`, `--tss`, and `--if` leaves planned TSS/IF unset. This also applies to unstructured events.
- Explicit `--tss` and `--if` override corresponding estimated values.
- On `edit-structure`, omitting `--auto-tss` preserves existing planned TSS/IF; it does not clear them.
- Actual post-workout TSS remains distinct from all planned estimates.

Confirm the athlete or coach's preference rather than applying a personal convention to every account. Keep individual planning preferences outside this reusable skill.
