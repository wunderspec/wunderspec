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
| `wunderspec rust`             |    ✅     |   ✅    |
| `wunderspec lean`             |    ❌     |   ✅    |

Rust translation uses the Wunderspec SaaS service and requires a token in every
edition. Offline JSON export is available without a token. See the
[Rust command guide](https://github.com/wunderspec/wunderspec/blob/main/docs/user-references/rust.md).

## 4. Release Provenance

- Release tag: `v0.141.1`
- Source commit: `6b9cd1dbbaccadc5846db03082e7a9b39868bcfb`

See [tests/README.md][] for the development test log captured at release time.

## 5. Latest Release Notes

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

## 6. License

Wunderspec is distributed under the Functional Source License, Version 1.1, with
an Apache 2.0 future license (FSL-1.1-ALv2). See [LICENSE][].

[cheatsheet]: https://wunderspec.com/cheatsheet
[five-minutes]: https://github.com/wunderspec/wunderspec/blob/main/docs/user-references/wunderspec-five-minutes.md
[bobs_log]: https://github.com/wunderspec/wunderspec/blob/main/docs/user-stories/bobs_log.md
[uv]: https://docs.astral.sh/uv/
[tests/README.md]: https://github.com/wunderspec/wunderspec/blob/main/tests/README.md
[LICENSE]: https://github.com/wunderspec/wunderspec/blob/main/LICENSE
