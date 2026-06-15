# Decorators

Wunderspec uses decorators to transform plain Python classes and functions
into symbolic objects. This page is a compact reference for all supported
decorators, grouped by purpose.

## Decorator quick-reference

| Decorator | Applies to | Purpose |
|---|---|---|
| `@expr` | function | Symbolic helper with auto-coercion and `LET`-binding |
| `@record` | class | Typed record constructor (`RecordExpr`) |
| `@union` | class | Tagged variant constructor (`UnionExpr`) |
| `@state` | class | State schema (variables + parameters) |
| `@action` | function | Transition or initialisation action |
| `@invariant` | function | Safety predicate (must always hold) |
| `@example` | function | Reachability predicate (must eventually hold) |
| `@temporal` | function | Liveness property (`TemporalExpr`) |
| `@coverage` | function | State-shape tracker for fuzzing / model checking |
| `@instance` | function | Concrete parameter factory for a `@state` class |

---

Before we start, let's import the necessary classes from our library:

<!-- name: test_decorators -->
```python
from typing import Annotated
from wunderspec import *
```

---

## Expression-level decorators

These decorators operate on functions and classes that produce symbolic
expressions and can be used anywhere in a spec.

---

### `@expr`

Decorates a helper function that computes a symbolic expression. Handles
argument coercion and optional `LET`-binding automatically.

<!-- name: test_decorators -->
```python
@expr
def add_one(n: int) -> int:
    return n + Val(1)

assert repr(value(add_one(Val(3)))) == "4"
assert repr(value(add_one(10)))     == "11"   # int auto-coerced
```

**Options:**

| Option | Default | Meaning |
|---|---|---|
| `cache_args=True` | `True` | Wrap each argument in a `LET` node to avoid AST duplication |
| `pure=True` | `False` | Assert the function never receives a `@state` object |
| `coerce=False` | — | Require callers to pass `Expr` instances; disable auto-coercion |

With `cache_args=False` the argument is inlined directly (no `LET` wrapper):

<!-- name: test_decorators -->
```python
@expr(cache_args=False)
def negate(b: bool) -> bool:
    return Not(b)

assert repr(negate(Val(True))) == "NOT(Lit(True))"
```

With `pure=True` passing a `@state` object raises `TypeError`:

<!-- name: test_decorators -->
```python
@expr(pure=True)
def square(x: int) -> int:
    return x * x

assert repr(value(square(Val(4)))) == "16"
```

With `coerce=False` both arguments and the return value must already be `Expr`:

<!-- name: test_decorators -->
```python
@expr(coerce=False)
def strict_not(b: BoolExpr) -> BoolExpr:
    return Not(b)

assert repr(value(strict_not(Val(False)))) == "True"

try:
    strict_not(True)   # raw bool is rejected
except TypeError as e:
    assert "@expr(coerce=False)" in str(e)
```

See [state-machine.md](state-machine.md) for how state objects are expanded
into `LET` aliases when passed to `@expr` functions.

---

### `@record`

Transforms a class with `Annotated[Expr, <type>]` fields into a symbolic
record type whose instances are `RecordExpr` values.

<!-- name: test_decorators -->
```python
@record
class Point:
    x: Annotated[Expr, int]
    y: Annotated[Expr, int]

p = Point(x=3, y=4)
assert repr(value(p))   == "Record(x=3, y=4)"
assert repr(value(p.x)) == "3"
```

All fields are required. Missing or extra fields raise at construction time:

<!-- name: test_decorators -->
```python
try:
    Point(x=1)            # y missing
except TypeError as e:
    assert "Missing required fields" in str(e)
```

The `@record` class can be used as a sort with `Var` and `AllRecords`.
See [records.md](records.md) for the full reference.

---

### `@union`

Transforms a class with `Variant[<type>]` fields into a symbolic union type.
Each field defines one variant; `Variant[Unit]` marks a no-payload variant.

<!-- name: test_decorators -->
```python
@union
class Option:
    Some: Variant[int]
    None_: Variant[Unit]

assert repr(value(Option.Some(42))) == "Some(42)"
assert repr(value(Option.None_())) == "None_"
```

Pattern-match on a union with `.match()`:

<!-- name: test_decorators -->
```python
x = Option.Some(7)
result = x.match(
    Some=lambda v: v * Val(2),
    None_=lambda: Val(0),
)
assert repr(value(result)) == "14"
```

See [unions.md](unions.md) for the full reference.

---

## State-machine decorators

These decorators are used on classes and functions that make up a full
Wunderspec state machine specification. They mark each piece of the spec so
that the CLI tools (`wunderspec check`, `wunderspec run`, `wunderspec fuzz`)
can find and execute them.

---

### `@state`

Transforms a class (subclassing `MachineStateBase`) into the state schema of
a machine. Fields annotated with `Annotated[Expr, <type>]` become
**variables** (mutable per transition); fields annotated with
`Annotated[Expr, <type>, PARAMETER]` become **parameters** (constant
across all transitions).

```python
from wunderspec.machine import PARAMETER, MachineStateBase, state

@state
class CounterState(MachineStateBase):
    N:       Annotated[Expr, int, PARAMETER]   # constant bound
    counter: Annotated[Expr, int]              # mutable variable
```

- Un-supplied fields initialise as symbolic `Var` expressions, so you can
  use the state object to write `@invariant` functions without providing
  concrete values.
- Variables may be assigned **at most once** per transition; a second
  assignment raises `AttributeError`.
- Parameters may never be assigned after construction.

See [state-machine.md](state-machine.md) for a full walk-through.

---

### `@action`

Marks a function as an action of a state machine. The first argument must
always be the context `c: Context[MyState]`.

```python
@action(init=True)
def init(c: Context[CounterState]):
    c.state.counter = Val(0)

@action(inline=False)
def increment(c: Context[CounterState]):
    c.state.counter = c.state.counter + Val(1)

@action
def step(c: Context[CounterState]):
    ...  # calls increment, decrement, etc. via alternatives
```

**Options:**

| Option | Default | Meaning |
|---|---|---|
| `init=True` | `False` | TLA+ conversion emits assignments without primes (initialisation action) |
| `inline=False` | `True` | Extract as a named TLA+ operator; required for `Enabled`, `WeakFair`, `StrongFair` |
| `coerce=False` | `True` | Require non-context arguments to be `Expr`; disable auto-coercion |

The `_action_name` attribute on the decorated function holds the original
function name, and `_inline` holds the `inline` flag. These are used by
`Enabled`, `WeakFair`, and `StrongFair`.

---

### `@invariant`

Marks a function as a safety invariant. The function takes one argument
(the state) and returns a `BoolExpr`. The model checker reports a violation
when the expression evaluates to `False`.

```python
@invariant
def non_negative(s: CounterState) -> BoolExpr:
    return s.counter >= Val(0)
```

The decorator sets `_is_invariant = True` on the function; the CLI uses this
attribute to discover invariants automatically. No runtime behaviour is
changed.

---

### `@example`

The dual of `@invariant`. Marks a function as an example predicate: the
model checker reports success when it finds a reachable state where the
expression evaluates to `True`.

```python
@example
def reaches_five(s: CounterState) -> BoolExpr:
    return s.counter == Val(5)
```

Sets `_is_example = True`. Useful for checking that a target state is
reachable, as opposed to checking that it is unreachable.

---

### `@temporal`

Marks a function as a temporal (liveness) property. The function returns a
`TemporalExpr`.

```python
@temporal
def progress(s: CounterState):
    return Always(Eventually(s.counter > Val(0)))
```

Sets `_is_temporal = True`. See [temporal.md](temporal.md) for the operators
available inside temporal properties.

---

### `@coverage`

Marks a function as a coverage predicate. The function takes one argument
annotated as a `MachineStateBase` subclass and must declare return type `Expr`.
The model checker or fuzzer uses it to track which distinct state "shapes" it
has explored.

```python
@coverage
def state_shape(s: CounterState) -> Expr:
    return s.counter
```

`@coverage` validates its signature at decoration time and raises `TypeError`
if the annotation is missing or incorrect.

---

### `@instance`

Marks a zero-argument factory function that returns a concrete state prototype
— a `@state` object with all parameters filled in. The CLI and tests use
`@instance` functions to discover which parameterisations to check.

```python
@instance
def small() -> CounterState:
    return CounterState(N=Val(10))
```

Sets `_is_instance = True`. Multiple `@instance` functions can coexist in the
same module; `find_instance_factories(module)` discovers them all.
