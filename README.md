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

- Release tag: `v0.136.1`
- Source commit: `6ef37ade3f08676708e0127d20ba14e313abbbb2`

See [tests/README.md][] for the development test log captured at release time.

## 5. Latest Release Notes

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

## 6. License

Wunderspec is distributed under the Functional Source License, Version 1.1, with
an Apache 2.0 future license (FSL-1.1-ALv2). See [LICENSE][].

[cheatsheet]: https://wunderspec.com/cheatsheet
[five-minutes]: https://github.com/wunderspec/wunderspec/blob/main/docs/user-references/wunderspec-five-minutes.md
[bobs_log]: https://github.com/wunderspec/wunderspec/blob/main/docs/user-stories/bobs_log.md
[uv]: https://docs.astral.sh/uv/
[tests/README.md]: https://github.com/wunderspec/wunderspec/blob/main/tests/README.md
[LICENSE]: https://github.com/wunderspec/wunderspec/blob/main/LICENSE
