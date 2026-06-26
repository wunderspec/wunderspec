![Wunderspec](https://raw.githubusercontent.com/wunderspec/wunderspec/main/assets/design/png/wunderspec-readme-header-dark-1200x480.png)

# Wunderspec

[![PyPI](https://img.shields.io/pypi/v/wunderspec.svg)](https://pypi.org/project/wunderspec/)
[![Lint](https://github.com/wunderspec/wunderspec/actions/workflows/lint.yml/badge.svg)](https://github.com/wunderspec/wunderspec/actions/workflows/lint.yml)
[![Build](https://github.com/wunderspec/wunderspec/actions/workflows/build.yml/badge.svg)](https://github.com/wunderspec/wunderspec/actions/workflows/build.yml)
[![Run Examples](https://github.com/wunderspec/wunderspec/actions/workflows/run-examples.yml/badge.svg)](https://github.com/wunderspec/wunderspec/actions/workflows/run-examples.yml)
[![Convert to TLA+](https://github.com/wunderspec/wunderspec/actions/workflows/convert-to-tla.yml/badge.svg)](https://github.com/wunderspec/wunderspec/actions/workflows/convert-to-tla.yml)

Wunderspec is a Python DSL for writing and checking executable specifications.

Distributed systems often fail due to unforeseen issues: message reordering,
race condition, stale observations, retry behavior, crashes and restarts,
network outages, vibe code. Adding more example tests is often not enough to
find these bugs before they make it to production. Specifications written in
Wunderspec surface these issues early, so that teams find design flaws before
they become production incidents.

This is **the open-core distribution**.

> :clipboard: **Wunderspec in 5 minutes:** the [Wunderspec in Five Minutes][five-minutes]
> gives you a quick overview of the core concepts.

> :clipboard: **Cheatsheet:** the [Wunderspec cheatsheet][cheatsheet]
> summarizes the DSL and CLI in a few pages — keep it open while you work.

> :bulb: **Using Quint?** Try this command:
> ```sh
> uv tool install wunderspec
> wunderspec convert --from=spec.qnt --to=spec.py --main=main
> ```

<div align="center">
  <img
    src="https://raw.githubusercontent.com/wunderspec/wunderspec/main/assets/design/svg/wunderspec_flower.svg"
    alt="Wunderspec Flower"
    width="70%">
</div>

## 1. Wunderspec in Action

Follow Bob in [his Wunderspec adventure][bobs_log].  There, Bob writes a
specification of a write-ahead log, finds a bug, replays the counterexample and
fixes the specification.

## 2. Installation

Using [uv][]:

```sh
uv tool install wunderspec
```

After installation, the CLI is available:

```sh
wunderspec --help
```

If you want to add Wunderspec as a dependency, just type:

```sh
uv add wunderspec
uv sync
```

## 3. Features

This open-core package includes the features marked ✅ in the **Open Core**
column. Premium-only command names stay visible for discoverability, but those
commands are not included in this package.

| Feature                       | Open Core | Premium |
| ----------------------------- | :-------: | :-----: |
| Symbolic expression evaluator |    ✅     |   ✅    |
| `wunderspec lint`             |    ✅     |   ✅    |
| `wunderspec run`              |    ✅     |   ✅    |
| `wunderspec replay`           |    ✅     |   ✅    |
| `wunderspec convert`          |    ✅     |   ✅    |
| `wunderspec check`            |    ✅     |   ✅    |
| `wunderspec with-tlc`         |    ✅     |   ✅    |
| `wunderspec with-apalache`    |    ✅     |   ✅    |
| `wunderspec fuzz`             |    ❌     |   ✅    |
| `wunderspec rust`             |    ❌     |   ✅    |
| `wunderspec lean`             |    ❌     |   ✅    |

## 4. Release Provenance

- Release tag: `v0.134.1`
- Source commit: `c9ee963d884f34db1ae946b8dec869d9c7a80c76`

See [tests/README.md][] for the development test log captured at release time.

## 5. Latest Release Notes

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

## 6. License

Wunderspec is distributed under the Functional Source License, Version 1.1, with
an Apache 2.0 future license (FSL-1.1-ALv2). See [LICENSE][].

[cheatsheet]: https://wunderspec.com/cheatsheet
[five-minutes]: https://github.com/wunderspec/wunderspec/blob/main/docs/user-references/wunderspec-five-minutes.md
[bobs_log]: https://github.com/wunderspec/wunderspec/blob/main/docs/user-stories/bobs_log.md
[uv]: https://docs.astral.sh/uv/
[tests/README.md]: https://github.com/wunderspec/wunderspec/blob/main/tests/README.md
[LICENSE]: https://github.com/wunderspec/wunderspec/blob/main/LICENSE
