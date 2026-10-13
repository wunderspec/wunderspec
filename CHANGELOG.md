# Changelog

## [0.141.1] -- 2026-10-12

Changes since public release v0.137.3.

- Changed (breaking): transfer format 1.1 replaces 1.0. Every instance parameter is
  carried as the expression its factory builds (a literal is a `lit` node), so instances
  with set, map, tuple, record, list, or union values (for example
  `CORRECT=Set(0, 1, 2)`) are no longer skipped, and `wunderspec rust run`, `check`, and
  `replay --instance NAME` work for them. Format 1.0 is no longer read or written.
- Added: when the service accepts another format version, `wunderspec rust` exits 69 and
  names both versions.
- Fixed: `wunderspec rust run`, `check`, and `replay` with an `--instance` that the
  export skipped blamed the cached model and suggested `--no-cache`. They now exit 65
  before contacting the service and point to the export warning; so does a specification
  with parameters whose instances were all skipped.
- Changed (breaking): `wunderspec rust` has the commands of the internal
  `wunderspec-rust`: `compile --from SPEC --to DIR`, `run SPEC`, `check SPEC
  --property P`, and `replay SPEC --property P`, with the same options and defaults.
  They translate on the service, build the model locally with Cargo (Rust 1.85 or
  newer), and run it; verdicts, statuses, and replay commands match `wunderspec-rust`.
- Added: translations are reused while the transfer document and `--init`, `--step`,
  and `--property` are unchanged, so other option changes never contact the service.
  They are kept in a per-user cache (`WUNDERSPEC_CACHE_DIR`, limited by
  `WUNDERSPEC_CACHE_MAX_SIZE`, default 2G) or in `--to DIR`; `--no-cache` translates
  again. Interrupted jobs are resumed; concurrent runs submit once.
- Changed (breaking): the 0.139 forms are replaced: `SPEC -o DIR` by `compile --from
  SPEC --to DIR --no-build`, `--export` by `export SPEC --to FILE`, `--document` by
  `submit FILE --to DIR`, and `--job` by `job JOB_ID --to DIR`.
- Changed (breaking): exit statuses: model outcomes keep 0-3, 101, 124, and 130; usage
  errors are 64, evaluation and translation failures 65, service refusals 69, refused
  output 73, network errors 74, stopped waiting 75, configuration errors 78, missing
  toolchain 79, build failures 80, and options a cached model rejects 81
  (`wunderspec rust --help-exit`).
- Changed: the wait for the service is `--wait-timeout` on `compile`, `run`, `check`,
  and `replay` (`--timeout` there is the model's time limit, as in `wunderspec-rust`);
  `submit` and `job` keep `--timeout`.
- Added: `wunderspec.rust_cli`: the option table and model driver shared with
  `wunderspec-rust` and the help of generated models.
## [0.139.0] -- unreleased

- Changed (breaking): `wunderspec rust` reads the service address, token, and
  optional extra trust root from a service configuration file: `--config FILE`,
  else `WUNDERSPEC_CONFIG`, else `~/.wunderspec/service.yaml` (keys `service`,
  `token`, `ca_file`). The file must be owned by the user with mode 600.
- Removed: `--service`, `--token-file`, `--ca-file`, `WUNDERSPEC_SERVICE_URL`, and
  `WUNDERSPEC_TOKEN`. The removed options exit with status 2 and point to
  `--config`. Export mode needs no configuration.
- Add `wunderspec.transfer`: format 1.0 JSON export, strict validation and safe AST
  import, source locations, and the schema shared with the translation service.
- Use AST constructors and their computed sorts for transfer validation; retain
  scope and document consistency checks without a separate sort-computation table.
- Move the backend-neutral gatherer and JSON encoder from wunderspec-lean
  `8cc1dcec2f7eb5f86345161a9a9bd677b2d72e66`; encoding uses per-export state.
- Replace the `wunderspec rust` stub with an HTTPS service client in every edition:
  local export, document submission, job polling/resume, and safe transactional
  artifact extraction. Translation requires a SaaS token.
- Add shared adversarial conformance tests, all-node and sort round trips, example
  schema checks, and HTTPS/archive security tests.
- Remove `wunderspec.ast.serialization`, `save_ast`, and `load_ast` without
  compatibility wrappers. JSON is the only supported AST transfer format.
- Production service URL configuration and publication are separate.

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

