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

> 📋 **Wunderspec in 5 minutes:** the [Wunderspec in Five Minutes][five-minutes]
> gives you a quick overview of the core concepts.

> 📋 **Cheatsheet:** the [Wunderspec cheatsheet][cheatsheet]
> summarizes the DSL and CLI in a few pages — keep it open while you work.

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

- Release tag: `v0.129.6`
- Source commit: `ab0a7ebd149ec98095c87f190c8c43616c1df187`

See [tests/README.md][] for the development test log captured at release time.

## 5. License

Wunderspec is distributed under the Functional Source License, Version 1.1, with
an Apache 2.0 future license (FSL-1.1-ALv2). See [LICENSE][].

[cheatsheet]: https://github.com/wunderspec/wunderspec/blob/main/docs/user-references/cheatsheet.html
[five-minutes]: https://github.com/wunderspec/wunderspec/blob/main/docs/user-references/wunderspec-five-minutes.md
[bobs_log]: https://github.com/wunderspec/wunderspec/blob/main/docs/user-stories/bobs_log.md
[uv]: https://docs.astral.sh/uv/
[tests/README.md]: https://github.com/wunderspec/wunderspec/blob/main/tests/README.md
[LICENSE]: https://github.com/wunderspec/wunderspec/blob/main/LICENSE
