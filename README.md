![Wunderspec](https://raw.githubusercontent.com/wunderspec/wunderspec/main/assets/design/png/wunderspec-readme-header-dark-1200x480.png)

# Wunderspec

Wunderspec is a Python DSL for writing and checking executable specifications.

Distributed systems often fail due to unforeseen issues: message reordering,
race condition, stale observations, retry behavior, crashes and restarts,
network outages, vibe code. Adding more example tests is often not enough to
find these bugs before they make it to production. Specifications written in
Wunderspec surface these issues early, so that teams find design flaws before
they become production incidents.

This is **the open-core distribution**.

> 📋 **Cheatsheet:** the [Wunderspec cheatsheet](https://github.com/wunderspec/wunderspec/blob/main/docs/user-references/cheatsheet.html)
> summarizes the DSL and CLI on a single page — keep it open while you work.

## 1. Wunderspec in Action

Follow Bob in [his Wunderspec adventure](https://github.com/wunderspec/wunderspec/blob/main/docs/user-stories/bobs_log.md). There,
Bob writes a specification of a write-ahead log, finds a bug, replays the
counterexample and fixes the specification.

## 2. Installation

Using [uv](https://docs.astral.sh/uv/):

```sh
uv add wunderspec
```

After installation, the CLI is available:

```sh
wunderspec --help
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

- Release tag: `v0.129.3`
- Source commit: `b21d0bd6cd54d3e259e418626727410c4875ac0b`

See [tests/README.md](https://github.com/wunderspec/wunderspec/blob/main/tests/README.md)
for the development test log captured at release time.

## 5. License

Wunderspec is distributed under the Functional Source License, Version 1.1, with
an Apache 2.0 future license (FSL-1.1-ALv2). See
[LICENSE](https://github.com/wunderspec/wunderspec/blob/main/LICENSE).
