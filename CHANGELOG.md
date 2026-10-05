# Changelog

## [0.137.3] -- 2026-10-05

Changes since public release v0.137.1.

- Fix AST pickle loading that could execute arbitrary code through `builtins.eval`
  and other globals. Allow only explicit AST and sort classes and the internal
  AST enums; reject pickle extensions that bypass the class allowlist.
- Deprecate `save_ast` and `load_ast` in favor of `wunderspec.transfer`, planned
  for v0.138.0. Load pickle files only if you produced them yourself.

## [0.137.1] -- 2026-07-26

Changes since public release v0.136.6.

- Add `items` to `Map` expressions.
- Document `Map.values` and `Map.items`.

## [0.136.6] -- 2026-07-10

Changes since public release v0.136.4.

Fix examples in the CI

## [0.136.4] -- 2026-07-06

Changes since public release v0.136.1.

- Add an inductive invariant in `examples/tendermint_single_indinv.py`.

## [0.136.1] -- 2026-07-04

Changes since public release v0.134.1.

- Add `--coverage NAME` to `wunderspec with-apalache`, passing the generated
  TLA+ operator for the selected `@coverage` function as `--view=<Oper>` for
  `check` and `simulate`.
- Allow `wunderspec with-apalache --max-steps 0` to check initial states.
- Write Apalache output to an `apalache.log` file while it runs, report the log
  path, and add `--verbose` to stream the log to the terminal immediately.
- Require `default=...` for collection-form `Max` and `Min`, e.g.
  `Max(s, default=0)`. The default is the reduce seed, so empty collections no
  longer need a separate size guard or `CHOOSE`-based seed.
- Render collection-form `Max` and `Min` reducers as TLAPS-friendly `CHOOSE`
  expressions in generated TLA+ while keeping reducer-based interpreter
  evaluation.
- Add TLA+ labels to `wunderspec convert` output for `@invariant` and
  `@example` operators, including nested predicate calls by default. Use
  `@invariant(inline=True)` or `@example(inline=True)` to inline nested
  predicate calls instead.
- Preserve definition docstrings when converting Wunderspec to TLA+ by emitting
  them as `\*` comments immediately before the generated operator definitions.
- Allow generator-form `Forall`, `Exists`, `SetIf`, `Set`, and `Map` calls to
  pass `name=` for readable TLA+ binder names while preserving internal unique
  binder identities.
- Short-circuit interpreter evaluation of `Implies(False, rhs)`, so the right
  side is not evaluated when the antecedent is false.
- Add `Max` and `Min` builtins. They take either several integers
  (`Max(a, b, c)`) or a single set or list of integers (`Max(s)`). Both are
  syntactic sugar — the multi-argument form expands to nested `Ite` and the
  collection form folds with `reduce` — so they work in the interpreter, TLC,
  and Apalache. `Max(s)`/`Min(s)` over an empty collection are undefined and
  raise, mirroring `CHOOSE` over an empty set; guard with
  `Ite(s.size == 0, default, Max(s))` when you need a fallback.

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

