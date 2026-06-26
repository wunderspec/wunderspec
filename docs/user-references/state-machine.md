# State machines

A Wunderspec state machine is a Python module that defines the **state
schema**, an **init action**, a **step action**, and optional **invariants**
and **properties**. The `wunderspec` CLI tools discover these by convention
and by decorator attributes.

Before we start, let's import the necessary classes from our library:

<!-- name: test_state_machine -->
```python
from wunderspec import *
from wunderspec.machine import (
    Context, MachineStateBase, Param, StateVar,
    action, coverage, example, instance, invariant, state, temporal,
)
```

---

## State schema — `@state`

The state schema is a class decorated with `@state` that lists all the
symbolic fields of the machine. It must subclass `MachineStateBase`.

Fields come in two flavours:

- **Variables** — `StateVar[T]`: mutable state that changes across
  transitions.
- **Parameters** — `Param[T]`: symbolic constants that are fixed for a given
  run (TLA<sup>+</sup> `CONSTANTS`).

The older `Annotated[Expr, T]` and `Annotated[Expr, T, PARAMETER]` forms are
still accepted, but `StateVar[T]` and `Param[T]` are preferred.

<!-- name: test_state_machine -->
```python
@state
class CounterState(MachineStateBase):
    N:       Param[int]      # upper bound — a constant
    counter: StateVar[int]  # the actual counter variable
```

Constructing the class with keyword arguments sets those fields to concrete
expressions. Fields not supplied are initialised to symbolic `Var`
expressions — useful for writing invariants:

<!-- name: test_state_machine -->
```python
# Concrete instance (all params provided, variables are symbolic Vars):
s = CounterState(N=Val(10))
assert isinstance(s.N, IntExpr)        # N = Lit(10)
assert s.counter.sort == IntSort()     # counter = Var("counter", IntSort())
```

### Variable assignment rules

Inside a transition, each **variable** field may be assigned **at most once**.
A second assignment to the same variable raises `AttributeError`. This enforces
the TLA<sup>+</sup> primed-variable discipline — after a transition, every
variable has exactly one new value.

**Parameters** are immutable: assigning to one raises `AttributeError` always.

---

## The init action — `@action(init=True)`

The init action initialises all variable fields. The TLA<sup>+</sup>
conversion emits plain assignments (`x = 0`) rather than primed assignments
(`x' = 0`).

<!-- name: test_state_machine -->
```python
@action(init=True)
def init(c: Context[CounterState]):
    c.state.counter = Val(0)
```

The first argument is always the **context** `c`. The state is accessed via
`c.state`. Variable assignments are written as plain Python attribute
assignments:

    c.state.counter = Val(0)   # sets counter in the next state

---

### Multiple init actions

A spec may split initialisation across several `@action(init=True)` functions
— each handles a different part of the state. The CLI combines them by calling
all of them in sequence before the first step. This pattern is useful when
different sub-systems are initialised by different helpers:

    @action(init=True)
    def init_counters(c: Context[MyState]):
        c.state.x = Val(0)
        c.state.y = Val(0)

    @action(init=True)
    def init_flags(c: Context[MyState]):
        c.state.ready = Val(False)

---

## The step action — `@action`

The step action defines the set of all legal transitions. It typically
dispatches among several sub-actions using `c.alternatives(...)`.

Actions decorated with `@action(inline=False)` are **extracted** as named
TLA<sup>+</sup> operators. This is required when the action is referenced by
`Enabled`, `WeakFair`, or `StrongFair`, which need the action's name to emit
correct TLA<sup>+</sup>. Actions that are only ever called internally can use
the default `inline=True`.

<!-- name: test_state_machine -->
```python
@action(inline=False)
def increment(c: Context[CounterState]):
    s = c.state
    c.assume(s.counter < s.N)
    s.counter = s.counter + Val(1)


@action(inline=False)
def decrement(c: Context[CounterState]):
    s = c.state
    c.assume(s.counter > Val(0))
    s.counter = s.counter - Val(1)


@action
def step(c: Context[CounterState]):
    alts = iter(c.alternatives("increment", "decrement"))
    with next(alts):
        increment(c)
    with next(alts):
        decrement(c)
```

---

## The `Context` protocol

The context `c` passed to every action provides primitives for
non-determinism, branching, and assumptions.

### `c.state`

The state **being built** for the next step. At the start of a transition
it is a copy of the current state; variable fields on it can be assigned once
to define the successor state.

### `c.one_of(set_expr, name=None)`

Pick a non-deterministic value from a set. Use it as a context manager:

    with c.one_of(Set(1, 2, 3), name="x") as x:
        # x is an Expr whose value is some element of {1, 2, 3}
        c.state.counter = x

- In random simulation, a random element is drawn.
- In model checking, every possible element is explored in separate paths.

### `c.alternatives(*names)`

Declare a set of mutually-exclusive branches. Return a tuple of
`Alternative` context managers — exactly one branch fires per transition:

    alts = iter(c.alternatives("up", "down", "reset"))
    with next(alts):
        pass  # up branch
    with next(alts):
        pass  # down branch
    with next(alts):
        pass  # reset branch

- In random simulation, one branch is picked randomly.
- In model checking, each branch is explored in a separate path.

### `c.split(condition)`

Split on a boolean condition, returning a `(then_alt, else_alt)` pair:

    (then_, else_) = c.split(s.counter > Val(5))
    with then_:
        pass  # counter > 5 is assumed here
    with else_:
        pass  # counter <= 5 is assumed here

### `c.assume(condition)`

Add a guard that prunes this branch if the condition is false. Corresponds to
`ASSUME` / `Guard` in TLA<sup>+</sup>:

    c.assume(s.counter > Val(0))   # discard this branch if counter == 0

### `c.cache(expr, name=None)`

Bind a sub-expression to a fresh name, preventing it from being inlined
repeatedly in the action AST. Useful when the same filtered set or computed
value is needed in several places:

    active = c.cache(s.msgs.filter(lambda m: m.active == Val(True)), name="active")
    c.assume(~active.is_empty)
    s.msgs = active

---

## Updating nested structures — direct assignment

Variable fields hold immutable expressions, but you update a nested structure
(a map entry, a record field, a list element, or any combination) with plain
Python assignment syntax on `c.state`. The field is updated on the spot:

    s = c.state
    s.pc[q]    = Val(PC.TRY)      # map entry
    s.flag[q]  = Val(True)
    s.req[p][q] = Val(0)          # nested map path
    s.chan.val = d                # record field
    s.cfg[k].timeout = Val(5)     # map entry, then record field
    s.queue[0] = Val(7)           # list element

Each keyed assignment writes back immediately, so a later assignment to the
same field refines the previous one. As with whole-field assignment, you may
read a field freely (`s.req[p][q]`, slicing, unpacking, etc.); only assignment
mutates the next state.

The right-hand side is coerced to the field's sort, so a plain Python literal
works — `Val(...)` is optional: `s.counter = 0`, `s.flag = True`,
`s.pc[q] = PC.TRY` are all accepted (the same as `Val(0)`, `Val(True)`,
`Val(PC.TRY)`). Wrap non-trivial right-hand sides in expressions as usual
(`s.counter = s.counter + Val(1)`).

### Insert vs. replace — the `UPSERT` marker

By default, a keyed assignment to a map field *replaces* an existing key: the
key must already exist, and tools may report an error otherwise. To allow
inserting a missing key (insert-or-update / "upsert"), declare the field with
the `UPSERT` marker:

    from wunderspec.machine import UPSERT

    @state
    class S:
        balance: StateVar[dict[str, int]]            # replace-only (default)
        sessions: StateVar[dict[str, Session], UPSERT]  # insert-or-update

    s.sessions[new_id] = make_session()              # inserts when missing

### Referring to the pre-update state under direct assignment

Direct assignment is the way to update one *or several* fields — every action
is a single atomic step, so updating several fields with successive assignments
needs nothing special.

Direct assignment is, however, *immediate*: after `s.x[k] = v`, a later read of
`s.x[k]` in the same step sees the **new** value. When a right-hand side must
read the *old* value of a field you also write, capture it in a plain local
**before** assigning — state expressions are immutable, so the local keeps the
pre-update value:

    # Swap two fields:
    old_x, old_y = s.x, s.y
    s.x = old_y
    s.y = old_x

    # Same idea for keyed updates — snapshot the field, then mutate:
    old_bal = s.balance
    s.balance[payer]    = old_bal[payer]    - amount
    s.balance[merchant] = old_bal[merchant] + amount   # reads the pre-update map

Writing `s.x = s.y; s.y = s.x` *without* the locals is a no-op swap: the second
statement reads the already-updated `s.x`. Binding the locals first avoids that.

When the snapshot is a large or repeatedly-used expression, bind it with
[`c.cache`](#ccacheexpr-namenone) instead of a bare local so it is emitted once.

### `state.editing()` (escape hatch)

`state.editing()` is a convenience that snapshots for you: it returns a context
manager whose reads all see the pre-update state, flushing the accumulated
updates on block exit. It does the same job as capturing locals, but across a
whole block:

    with c.state.editing() as upd:
        upd.x = c.state.y      # reads the old y
        upd.y = c.state.x      # reads the old x (pre-update)

Reach for it only when several interdependent fields make manual snapshots
awkward. For independent fields and keys — the overwhelmingly common case —
plain direct assignment is preferred.

### Expression-level `.edit()` (outside actions)

Plain expressions — maps, records, tuples, lists — also expose `.edit()`.
This returns an `UpdatesBuilder` whose `.result` property yields the updated
expression. Use this to build a modified copy of a value without being inside
a state machine action:

    upd = evm.edit()
    upd.balances[investor]              = evm.balances[investor] - amount
    upd.balances[ponzi.currentInvestor] = evm.balances[ponzi.currentInvestor] + amount
    new_evm = upd.result

---

## Invariants and properties

### `@invariant`

A function that takes the state and returns a `BoolExpr`. The model checker
reports a violation when this is `False` in any reachable state:

<!-- name: test_state_machine -->
```python
@invariant
def non_negative(s: CounterState) -> BoolExpr:
    return s.counter >= Val(0)

assert getattr(non_negative, "_is_invariant") is True
```

### `@example`

The dual of `@invariant`. The model checker searches for a reachable state
where this is `True`:

<!-- name: test_state_machine -->
```python
@example
def reaches_five(s: CounterState) -> BoolExpr:
    return s.counter == Val(5)

assert getattr(reaches_five, "_is_example") is True
```

When you check an `@example` with `run`, `check`, or `fuzz`, finding a witness
state prints `Example found …` and exits with code `2`. Finding none prints
`warning: No examples found …` and exits with code `3` — an unmet reachability
goal is reported as a warning, not a success. (For an `@invariant`, no violation
is a genuine `success` and exits `0`; a violation exits `1`.)

### `@temporal`

Declares a liveness property as a `TemporalExpr`:

<!-- name: test_state_machine -->
```python
@temporal
def progress(s: CounterState):
    return Always(Eventually(s.counter > Val(0)))

assert getattr(progress, "_is_temporal") is True
```

---

## Parameter factories — `@instance`

An `@instance` function is a zero-argument factory that returns a concrete
state prototype with all parameters filled in. The CLI uses `@instance`
functions to discover which configurations to check:

<!-- name: test_state_machine -->
```python
@instance
def small() -> CounterState:
    return CounterState(N=Val(5))

assert getattr(small, "_is_instance") is True
```

Multiple `@instance` functions can coexist in the same module. Pass
`--instance=small` to the CLI to select one.

---

## Coverage tracking — `@coverage`

A coverage function exposes a state "shape" expression that the fuzzer and
model checker use to track which distinct abstract states have been visited.
The function must take one `MachineStateBase` argument and return `Expr`:

<!-- name: test_state_machine -->
```python
@coverage
def shape(s: CounterState) -> Expr:
    return s.counter

assert getattr(shape, "_is_coverage") is True
```

---

## Sub-machines — `SubMachine`

A `SubMachine` embeds one specification into another by mapping field names.
This is the Python analog of TLA<sup>+</sup>'s `INSTANCE … WITH` construct.

### Defining a sub-machine

Import `SubMachine` and create a mapping from sub-spec field names to
parent-spec field names:

    from wunderspec.submachine import SubMachine

    # INSTANCE Channel WITH Data <- Message, chan <- cin
    InChan = SubMachine(Data="Message", chan="cin")

### Using in actions — `SubMachine(c)`

Call the sub-machine with the parent context to get a wrapped context whose
`.state` translates field names. Pass this wrapped context to the sub-spec's
actions:

    @action(init=True)
    def init(c: Context[FifoState]):
        channel.init(InChan(c))     # channel sees "chan" → reads/writes "cin"

### Using in invariants — `SubMachine.view(s)`

Call `.view(s)` with the parent state to get a read-only view with translated
field names. Pass this to the sub-spec's invariants or predicates:

    @invariant
    def type_invariant(s: FifoState) -> BoolExpr:
        return And(
            channel.type_invariant(InChan.view(s)),
            ...
        )

### Full example (FIFO with two channels)

See `examples/fifo.py` for a complete example that instantiates two channels
(`InChan` and `OutChan`) within a FIFO specification using `SubMachine`.

---

## Running the machine

The CLI commands follow a naming convention. Given a module `counter.py`
containing the definitions above:

```sh
# Random walk (simulation):
uv run wunderspec run --init=init --step=step --instance=small counter.py

# Model checking with an invariant:
uv run wunderspec check \
    --init=init --step=step \
    --property=non_negative \
    --instance=small \
    --max-steps=20 \
    counter.py

# Search for a reachable example:
uv run wunderspec check \
    --init=init --step=step \
    --property=reaches_five \
    --instance=small \
    --max-steps=20 \
    counter.py

# TLC model checking for a fixed @instance:
uv run wunderspec with-tlc \
    --init=init --step=step \
    --property=non_negative \
    --instance=small \
    counter.py

# Apalache bounded model checking for a fixed @instance:
uv run wunderspec with-apalache \
    --init=init --step=step \
    --property=non_negative \
    --instance=small \
    --max-steps=20 \
    counter.py
```

The `--bound` flag limits the branching factor at each step; combine it with
`--no-shuffle` to get a deterministic DFS order:

```sh
uv run wunderspec check \
    --init=init --step=step \
    --property=non_negative \
    --instance=small \
    --max-steps=20 --bound=100 \
    counter.py
```

### Trace length and retries

A `run` trace grows until it reaches `--max-steps` or it gets stuck (no enabled
step can be sampled). Because the random walk picks an action blindly and a pick
may be disabled, each trace has a retry budget of `--max-retries-per-step ×
--max-steps`; a failed pick consumes one retry, and the trace is cut when the
budget is exhausted (or `--max-steps` is reached). Raise `--max-retries-per-step`
(default 30) for specs where progress depends on hard-to-hit guards — a low value
makes traces die early. At the end, `run` prints
`Trace length statistics: max=…, min=…, average=…` over all sampled traces, so
you can see at a glance whether traces are reaching full length.

By default both `run` and `check` stop after the first invariant violation or
found example. Use `--max-findings N` to report up to `N` of them. For `run`
this keeps the random walk going until `N` matches accumulate; for `check` the
exhaustive DFS collects up to `N` distinct counterexamples / examples, each with
its own replay schedule:

```sh
uv run wunderspec check \
    --init=init --step=step \
    --property=reaches_five \
    --instance=small \
    --max-findings=3 \
    counter.py
```

To consume the found traces programmatically, `run` and `check` accept
`--out-itf <path>`, which streams each finding as an [ITF](https://apalache-mc.org/docs/adr/015adr-trace.html)
trace in **NDJSON** (one JSON document per line) as soon as it is discovered — no
separate `replay` step. Pass `--out-itf -` to stream to **stdout**; in that mode
all other output (info messages, human-readable traces, progress) goes to
**stderr**, so stdout carries only NDJSON and pipes cleanly:

```sh
uv run wunderspec check \
    --init=init --step=step \
    --property=reaches_five \
    --instance=small \
    --max-findings=10 --out-itf - \
    counter.py | jq -c '.["#meta"].example_step'
```

### Action profiling

By default, `run` and `check` accumulate a per-action profile and print a
compact `fired/tried` table at the end, sorted by action name:

```text
Action profile (fired/tried):
enter 0/135 (0%)        exit_cs 0/148 (0%)        receive_ack 0/19 (0%)
receive_release 0/32 (0%)  receive_request 11/21 (52%)  request 49/124 (40%)
```

For each action, **fired** is the number of times its body completed without
violating an assumption, **tried** is the number of times it was entered, and the
percentage is the fire rate (`fired/tried`). When color is enabled, actions whose
fire rate is at or near 0% are highlighted in **red** — they almost never fire and
are likely dead or only rarely enabled, which is often worth investigating. Only
actions declared `@action(inline=False)` keep a distinct identity during
execution and are profiled; inlined actions (the default) are flattened into
their caller and do not get their own row. A fully inlined spec therefore has
nothing to profile, and no table is printed. Every non-inline action reachable
from `init`/`step` is listed, even ones that were never reached during the search
(shown as `0/0`), which makes dead or unreachable actions easy to spot. The
counts are eager: a nested action that succeeds is counted as fired even if an
enclosing action later fails.

Pass `--no-action-profiling` to disable the accumulation and the table (this also
restores the slightly faster fully-inlined execution path). Profiling is not
available with `run --debug`, which re-runs the Python action functions directly.

---

## Complete minimal example

```python
"""minimal_counter.py — a self-contained Wunderspec state machine."""

from wunderspec import *
from wunderspec.machine import (
    Context, MachineStateBase, Param, StateVar,
    action, instance, invariant, state,
)


@state
class CounterState(MachineStateBase):
    N:       Param[int]
    counter: StateVar[int]


@action(init=True)
def init(c: Context[CounterState]):
    c.state.counter = Val(0)


@action(inline=False)
def increment(c: Context[CounterState]):
    s = c.state
    c.assume(s.counter < s.N)
    s.counter = s.counter + Val(1)


@action
def step(c: Context[CounterState]):
    with next(iter(c.alternatives("increment"))):
        increment(c)


@invariant
def bounded(s: CounterState) -> BoolExpr:
    return s.counter <= s.N


@instance
def small() -> CounterState:
    return CounterState(N=Val(5))
```
