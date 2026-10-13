# Rust translation

`wunderspec rust` turns your specification into a Rust model that simulates and
model-checks it quickly. It evaluates your Python specification locally, sends its
declarative form to the Wunderspec translation service, builds the generated project
with Cargo on your machine, and runs it:

```text
wunderspec rust run examples/dekker.py --max-samples 500 --seed 7
wunderspec rust check examples/dekker.py --property mutual_exclusion
```

Translation happens on the service, which counts it against your quota. Everything
else happens locally, and the translation is **reused** as long as the specification
(including the modules it imports) and `--init`, `--step`, and `--property` stay the
same. Changing any other option (samples, seeds, workers, time and memory limits,
visited-state strategy, output files, or the build profile) never contacts the
service. `--no-cache` translates again, which is how you pick up a newer translator.

Building needs Rust 1.85 or newer (`cargo` on your `PATH`, for example from
[rustup](https://rustup.rs)). The first build downloads the crates the project depends
on; later builds work offline.

## Commands

| Command | What it does |
|---|---|
| `compile --from SPEC --to DIR` | Translate (or reuse) into `DIR` and build it there; `--no-build` only writes the project |
| `run SPEC` | Sample random traces |
| `check SPEC --property P` | Explore the state space exhaustively |
| `replay SPEC --property P (--from-schedule FILE \| --seed N)` | Replay a finding |
| `export SPEC --to FILE` | Write the transfer document without contacting the service |
| `submit DOCUMENT --to DIR` | Translate an exported document into `DIR` |
| `job JOB_ID --to DIR` | Wait for, or download, a job submitted earlier |

`compile`, `run`, `check`, and `replay` accept the same options as the internal
`wunderspec-rust` tool, with the same defaults: run `wunderspec rust COMMAND --help`
for the full list. Options marked `[re-translates]` (`--init`, `--step`,
`--property`) change the translation; all others are passed to the local build or
to the model.

`--init` and `--step` default to `init` and `step`. Select differently named
operators explicitly (e.g. `--init Init --step Next`). `--property` selects an
invariant or example.

Every `@instance` is sent, including those with set-, map-, or other composite-valued
parameters such as `CORRECT=Set(0, 1, 2)` (see [Transfer format](transfer.md)), and
`--instance` works as with `wunderspec-rust`. A service that accepts another transfer
format version refuses the document; `wunderspec rust` then exits 69 and names both
versions. If the export skips an instance (its warning gives the reason), `run`, `check`,
and `replay` exit 65 before contacting the service when `--instance` names it, or when a
specification with parameters has no instance left.

## Service configuration

The service address and your token live in a service configuration file,
`~/.wunderspec/service.yaml` by default. It contains your token, so create it
accessible only to you before writing it:

```text
mkdir -p ~/.wunderspec
install -m 600 /dev/null ~/.wunderspec/service.yaml
```

```yaml
service: https://YOUR_SERVICE_HOST
token: YOUR_SAAS_TOKEN
ca_file: test-ca.pem   # optional
```

| Key | Required | Meaning |
|---|---|---|
| `service` | yes | HTTPS service URL, without credentials, query, or fragment |
| `token` | yes | Your SaaS token: 1-4096 printable ASCII characters |
| `ca_file` | no | PEM trust root added to the system trust store, for example the private CA of a test service. A leading `~` is expanded; other relative paths are relative to the configuration file |

Other keys, duplicate keys, and non-string values are errors. The file must be a
regular file owned by you with no group or other permissions (`chmod 600`);
otherwise the command refuses it. On Windows, keep it in your user profile.
Errors name the file and key but never print the token. Tokens are sent only in
the Authorization header to `service`; there is no option or environment
variable that carries a token or a service address.

Use one file per service and select it with `--config FILE`, or with the
`WUNDERSPEC_CONFIG` environment variable when `--config` is not given. In CI, write
the file from a secret with mode 600 and export `WUNDERSPEC_CONFIG` with its path.
An empty `WUNDERSPEC_CONFIG` is an error.

A cached translation needs no configuration file: if none exists and exactly one
cached translation matches, it is used.

## The translation cache

Without `--to`, translations and their builds are kept in a per-user cache:
`~/.cache/wunderspec/rust` on Linux (`$XDG_CACHE_HOME/wunderspec/rust` if set),
`~/Library/Caches/wunderspec/rust` on macOS, and
`%LOCALAPPDATA%\wunderspec\cache\rust` on Windows. Set `WUNDERSPEC_CACHE_DIR` to use
another directory. The cache is private to you (mode 700), and is limited to
`WUNDERSPEC_CACHE_MAX_SIZE` (default `2G`): beyond it, the least recently used
translations are removed, never one that is in use.

Each run says when it reuses a translation:

```text
using translation from 2026-10-11T10:00:00Z (job …, translator 0.68.0, runtime 0.53.0)
```

Several runs at once share one translation; a translation that was interrupted is
resumed by the next run instead of being submitted again. Failed translations are not
cached.

## Building once and running the model yourself

`compile` keeps the project and its build in a directory you choose:

```text
wunderspec rust compile --from examples/dekker.py --to model/dekker
model/dekker/target/fast/wunderspec-model --help
model/dekker/target/fast/wunderspec-model run --max-samples 100
```

Compiling again with an unchanged specification prints `binary is up to date`.
`--build-profile release` (or `profiling`) builds another binary next to the first.
After you edit the specification, compiling again replaces the project with the new
translation and rebuilds it. `DIR` must be empty or hold a project that `wunderspec
rust` made for the same specification file and service; anything else is refused
unless you pass `--force`, which replaces its contents.

The generated `wunderspec-model` documents its own options (`--help`, `COMMAND
--help`) and versions (`--version`), and reports usage errors with exit status 64.

## Findings and replay

When `run` or `check` finds a violation or an example, it prints a command that
replays it from the cached translation, without the service:

```text
Fast replay command: wunderspec rust replay examples/dekker.py --from-schedule … --property mutual_exclusion
```

With `--compat`, it also prints a `wunderspec replay` command for the Python
interpreter's trace viewer. With `--out-itf -`, traces stream to standard output and
every other message goes to standard error.

## Export and submit separately

Export needs neither a configuration file nor a service connection. Exporting runs the
Python specification, so use a local specification you trust.

```text
wunderspec rust export examples/dekker.py --to dekker.json
wunderspec rust submit dekker.json --to generated/dekker
```

Exports have stable key order and indentation. Submissions always use compact JSON,
including when `submit` reads a large indented export. The compact document must fit
the 5 MiB transfer limit. Reading a document does not execute Python or import the
producer's modules.

## Waiting and output

Commands print the job ID and status changes while polling, starting at a one-second
interval and backing off to five seconds. The wait is limited by `--wait-timeout`
(`compile`, `run`, `check`, `replay`) or `--timeout` (`submit`, `job`), 600 seconds by
default; in `run` and `check`, `--timeout` is the model's time limit, as in
`wunderspec-rust`. Ctrl-C, the wait limit, or a connection failure after submission
prints a command that resumes the same job.

`submit` and `job` write into an absent or empty directory; `--force` replaces
archive files in an existing directory and preserves unrelated regular files.
Archives are validated and staged before installation: paths, file types,
duplicates, sizes, compression ratios, CRC checks, and the manifest's job ID must all
pass.

Failed translations print curated diagnostics as `FILE:LINE:COL: CODE: MESSAGE`.
Columns are zero-based, matching the transfer format. For quota and busy responses,
the message includes the Retry-After value.

## Exit statuses

| Exit | Meaning |
|---|---|
| 0 | Success; for `run`, `check`, and `replay`: no violation or example found |
| 1 | Invariant violated |
| 2 | Example found |
| 3 | Model memory limit exceeded |
| 64 | Usage error |
| 65 | The specification could not be evaluated, exported, or translated |
| 69 | The service refused: authentication, quota, busy, or format version |
| 73 | Output directory refused |
| 74 | Network or TLS error, or an invalid archive |
| 75 | Stopped waiting; the job continues (resume with the printed command) |
| 78 | Service configuration error |
| 79 | Rust toolchain missing or older than 1.85 |
| 80 | Local build failed |
| 81 | The model rejected an option: run again with `--no-cache` |
| 101 | The model panicked |
| 124 | Model time limit reached |
| 130 | Interrupted while evaluating, building, or running the model |

`wunderspec rust --help-exit` prints this table.

See [Transfer format](transfer.md) for the declarative representation.
