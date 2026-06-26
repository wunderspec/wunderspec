# Changelog

## [0.134.1] -- 2026-06-26

Changes since public release v0.132.2.

- When an `@example` property is checked but no witness state is found, `run`,
  `check`, `fuzz`, `replay`, `with-tlc`, and `with-apalache` now report the
  outcome as `warning: No examples found …` (previously the misleading
  `success: …`) and exit with the dedicated code `3`. The exit-code scheme is now
  `0` = clean / no predicate, `1` = invariant violation, `2` = example found,
  `3` = example not found. Invariant checks are unaffected: holding an invariant
  is still `success` / exit `0`.
- Add per-action profiling to `wunderspec run` and `check`. By default they now
  print a compact `fired/tried (pct%)` table, sorted by action name, counting
  how many times each non-inline (`@action(inline=False)`) action was entered
  (tried) and completed without violating an assumption (fired). Actions whose
  fire rate is at or near 0% are highlighted in red when color is enabled.
  Pass `--no-action-profiling` to disable the accumulation and the table.
- Improve `wunderspec run` trace coverage on specifications whose actions depend
  on hard-to-hit guards. Add `--max-retries-per-step` (default 30): the per-trace
  retry budget is `--max-retries-per-step × --max-steps`, and a trace is cut only
  when it reaches `--max-steps` or exhausts that budget. The earlier
  consecutive-failure cutoff, which abandoned still-progressing traces too early,
  has been removed. `run` now also prints
  `Trace length statistics: max=…, min=…, average=…` at the end.

