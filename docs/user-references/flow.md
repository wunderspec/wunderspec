# Imperative flow builder

Writing deeply nested `Ite` (if-then-else) expressions in a purely functional
style can be hard to read.  The `flow` module provides an **imperative**
builder that compiles a sequence of `with flow.if_(...):` blocks into the
equivalent `IteExpr` AST.

## Imports

    from wunderspec.flow import flow, with_flow

`with_flow` is the decorator; `flow` is the thread-local builder object you
call inside the decorated function.

## `@with_flow`

Decorate any function that constructs a symbolic expression imperatively.
The decorator creates a fresh `FlowBuilder`, makes it available via `flow`,
and assembles the final expression when the function returns.

    @with_flow
    def my_expr(x: Expr, y: Expr) -> Expr:
        with flow.if_(x > Val(0)):
            flow.return_(x)
        flow.return_(y)
        return flow.end()

The `return flow.end()` at the bottom is required by the type checker but is
never actually reached at runtime — `flow.return_()` raises an internal
exception that the decorator catches.

## `flow.if_(condition)`

Open a new conditional branch.  The body of the `with` block is the
**then** branch:

    with flow.if_(x > Val(0)):
        flow.return_(x * Val(2))

Multiple `with flow.if_(...)` blocks in sequence are chained as
if-then-else: the second condition becomes the "else if" branch:

    with flow.if_(x > Val(10)):
        flow.return_(Val(2))
    with flow.if_(x > Val(0)):
        flow.return_(Val(1))
    flow.return_(Val(0))
    return flow.end()

## `flow.else_()`

Introduce an explicit else branch after a completed `with flow.if_(...)`
block:

    with flow.if_(x > Val(0)):
        flow.return_(x)
    flow.else_()
    flow.return_(Val(0))
    return flow.end()

An explicit `flow.else_()` is optional — a bare `flow.return_(...)` after
a completed `if_` block serves as the else branch automatically.

## `flow.return_(expr)`

Set the symbolic value for the current branch and close it.  Must appear
inside a `with flow.if_(...):` body or in the "else" position after one.

## `flow.end()`

Assemble the accumulated `IteNode` tree into a single `Expr`.  Called at
the end of the function as `return flow.end()` to satisfy type checkers.

## Complete example

The following computes `max(a, b)` symbolically:

<!-- name: test_flow -->
```python
from wunderspec import *
from wunderspec.flow import flow, with_flow


@with_flow
def symbolic_max(a: Expr, b: Expr) -> Expr:
    with flow.if_(a >= b):
        flow.return_(a)
    flow.return_(b)
    return flow.end()


assert repr(value(symbolic_max(Val(3), Val(7)))) == "7"
assert repr(value(symbolic_max(Val(9), Val(2)))) == "9"
```

## Chained conditions (if / elif / else)

Sequential `with flow.if_` blocks are automatically chained: after one branch
closes, the next `if_` becomes its else-branch — equivalent to `if/elif/else`:

<!-- name: test_flow -->
```python
@with_flow
def classify(x: Expr) -> Expr:
    with flow.if_(x > Val(10)):
        flow.return_(2)
    with flow.if_(x > Val(0)):
        flow.return_(1)
    flow.return_(0)
    return flow.end()


assert repr(value(classify(Val(15)))) == "2"
assert repr(value(classify(Val(5))))  == "1"
assert repr(value(classify(Val(-1)))) == "0"
```

Both `flow.if_(...)` and `flow.return_(...)` auto-coerce raw Python literals, so
the conditions and returned values above can be plain `int`/`bool` rather than
`Val(...)` (a non-boolean `flow.if_` condition is rejected with a `TypeError`).

## Error conditions

`FlowError` is raised when:

- `flow` is used outside a `@with_flow` function.
- `else_()` is called twice for the same branch.
- `return_()` or `end()` is called in an unexpected state.
- The function ends without all branches being closed.

## When to use `@with_flow` vs. functional style

| Style | Good for |
|---|---|
| `@with_flow` | Long chains of conditions, code that resembles an imperative algorithm |
| `Ite(cond, then_, else_)` | Short, inline expressions; one level of branching |
| `.if_(...).else_(...)` chain | Short expressions with method chaining |

All three styles produce identical AST nodes.
