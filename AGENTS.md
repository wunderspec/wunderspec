# AGENTS.md -- CLAUDE.md

This file provides guidance to AI tools when working with code in this
repository.

## Project Overview

Wunderspec is a temporal specification-as-code framework for Python 3.11+,
inspired by TLA+. It provides a DSL for symbolic expressions, specifications,
and state machines using operator overloading and decorators. It also provides
CLI tools for random execution, model checking, and fuzzing.

## Execution Plans

You can find active (open) features in [active](./docs/exec-plans/active/).  The
completed features can be found in [complete](./docs/exec-plans/complete/), for
reference.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md).

## Development Commands and Coding Rules

See [DEVELOPMENT.md](DEVELOPMENT.md).

Before committing, run the CI lint checks:

```sh
PYENV_VERSION=wunderspec pyenv exec uv run black --check --diff .
PYENV_VERSION=wunderspec pyenv exec uv run isort --check-only --diff .
PYENV_VERSION=wunderspec pyenv exec uv run flake8 wunderspec tests
PYENV_VERSION=wunderspec pyenv exec uv run mypy wunderspec
```

## Bumping the version

NEVER hand-edit the version. The version lives in several files that must stay in
sync (`pyproject.toml`, `wunderspec/__init__.py`, the demo badge in
`demos/jupyterlite/site/index.html`, the `CHANGELOG.md` header, and `uv.lock`).
Editing only some of them makes CI fail — e.g. the `check-demos` job rejects a
demo badge that does not match `pyproject.toml`.

Always use the helper, which updates every location and refreshes the lock:

```sh
make bump-version VERSION=x.y.z
```

Then replace the generated `TODO` line in the new `CHANGELOG.md` header with the
real release notes. See the "Versioning" section of [DEVELOPMENT.md](DEVELOPMENT.md)
for details.

## Git and branch hygiene

Do not rewrite history that may already be published, and never lose the user's
pushed commits.

- **Never force-push.** Do not run `git push --force`, `git push -f`, or
  `git push --force-with-lease`. A normal `git push` must always fast-forward; if
  it would not, stop and ask the user.
- **Never rewrite a commit that is on `origin`.** Avoid `git commit --amend`,
  `git rebase`, and `git reset` on commits that have been pushed. Before
  amending/rebasing, confirm the commit is local-only:
  `git merge-base --is-ancestor origin/<branch> <branch>` and
  `git log origin/<branch>` show what the remote already has.
- **Add fixes as new commits on top.** When more changes are needed on a branch
  that is already pushed, commit them on top of `origin/<branch>` so the push
  fast-forwards — do not fold them into an existing pushed commit.
- **If a branch has diverged** from `origin/<branch>` (e.g. after a local amend),
  recover without force-pushing: `git reset --mixed origin/<branch>`, then
  re-commit the working-tree changes as new commits on top of the remote tip.
- **Do not commit or push unless the user asks**, and never push on the user's
  behalf without explicit confirmation.

## User Reference Manuals

See [User Reference](./docs/user-references/).

## Examples

See [Examples](./examples/).

## Translation from TLA+

See [From TLA+](./docs/references/from-tla-llms.md).

## Translation from Quint

See [From Quint](./docs/references/from-quint-llms.md).
