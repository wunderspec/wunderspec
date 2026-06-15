# Temporal expressions

Wunderspec supports a subset of LTL/TLA<sup>+</sup> temporal operators for
expressing liveness and safety properties.  Temporal expressions have sort
`TemporalSort` and appear as `@temporal` properties on state machines.

Before we start, let's import the necessary classes from our library:

<!-- name: test_temporal -->
```python
from wunderspec import *
```

## Always and Eventually

`Always(P)` means "P holds in every state of the execution" (`□P` / `[]P`).
`Eventually(P)` means "P holds in some future state" (`◇P` / `<>P`):

<!-- name: test_temporal -->
```python
assert repr(Always(Val(True)))     == "Always(Lit(True))"
assert repr(Eventually(Val(False))) == "Eventually(Lit(False))"
```

Both accept a `BoolExpr` or `TemporalExpr` argument. Nesting is allowed:

<!-- name: test_temporal -->
```python
# "always eventually P" — P keeps recurring
ag_p = Always(Eventually(Val(True)))
assert repr(ag_p) == "Always(Eventually(Lit(True)))"
```

## Temporal And, Or, Not, Implies

The temporal connectives `AndT`, `OrT`, `NotT`, and `ImpliesT` produce
`TemporalExpr` formulas. Use the non-`T` connectives for Boolean expressions.

<!-- name: test_temporal -->
```python
t1 = Always(Val(True))
t2 = Eventually(Val(False))

assert repr(AndT(t1, t2))     == "AND(Always(Lit(True)), Eventually(Lit(False)))"
assert repr(OrT(t1, t2))      == "OR(Always(Lit(True)), Eventually(Lit(False)))"
assert repr(NotT(t1))         == "NOT(Always(Lit(True)))"
assert repr(ImpliesT(t1, t2)) == "IMPLIES(Always(Lit(True)), Eventually(Lit(False)))"
```

A plain `BoolExpr` argument is lifted to `TemporalSort` automatically when
mixed with temporal arguments:

<!-- name: test_temporal -->
```python
inv = Val(True)            # BoolExpr
live = Eventually(inv)     # TemporalExpr
assert repr(AndT(inv, live)) == "AND(ToTemporal(Lit(True)), Eventually(Lit(True)))"
```

## Enabled

`Enabled(action_expr)` — or `Enabled(action_fn, *args)` for a named action
— states that the given action can fire in the current state. It corresponds
to `ENABLED A` in TLA<sup>+</sup>:

```python
# With a named @action(inline=False) function:
# Enabled(send, process_id)
#
# With an action expression directly:
# Enabled(action_expr)
```

The named-action form requires the action to be decorated with
`@action(inline=False)`. See the machine documentation for full details.

## Weak fairness — `WeakFair`

`WeakFair(action_fn, *args, vars=(...))` expresses that if action `A` is
continuously enabled, it must eventually fire. It corresponds to `WF_vars(A)`
in TLA<sup>+</sup>:

```python
# WeakFair(send, p, vars=("msgs",))
# Reads: if send(p) is always enabled, it eventually fires,
#        where msgs is the variable that stutters.
```

## Strong fairness — `StrongFair`

`StrongFair(action_fn, *args, vars=(...))` expresses that if action `A` is
repeatedly enabled, it must eventually fire. It corresponds to `SF_vars(A)`:

```python
# StrongFair(receive, p, vars=("msgs",))
# Reads: if receive(p) is enabled infinitely often, it eventually fires.
```

Both fairness constructors accept either:

1. An `@action(inline=False)` decorated function followed by its arguments
   and `vars=` stuttering variable names.
2. An action `Expr` followed by string variable names (backward-compatible
   form):

```python
# WeakFair(action_expr, "x", "y")   # old form, vars given as positional strings
# WeakFair(action_fn, p, vars=("x", "y"))   # preferred form
```

## Temporal quantification

`Forall` and `Exists` lift to temporal when their body is a `TemporalExpr`.
This lets you express fairness over a parametric set of processes:

```python
# Always(Forall(WeakFair(send, p, vars=("msgs",)) for p in PROCS))
# Reads: every process p satisfies weak fairness for send.
```

See [quantifiers.md](quantifiers.md) for the generator-expression syntax.

## Use in specs

Temporal properties are declared on a `@state` class using the `@temporal`
decorator:

```python
@state
class MySpec:
    ...

    @temporal
    def liveness(self) -> TemporalExpr:
        return Always(Eventually(self.done == Val(True)))
```

The `wunderspec check` CLI verifies these properties using model checking.
