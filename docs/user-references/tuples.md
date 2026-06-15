# Tuple expressions

Tuples are fixed-length, ordered, heterogeneous sequences. Unlike lists,
different positions can hold values of different sorts, and the length is
fixed at construction time. Tuples are **immutable**.

Before we start, let's import the necessary classes from our library:

<!-- name: test_tuples -->
```python
from wunderspec import *
```

## Creating a tuple

`Tuple(*elements)` takes at least one element. Wrap literals with `Val()`:

<!-- name: test_tuples -->
```python
assert repr(Tuple(Val(1), Val(True)))      == "Tuple(Lit(1), Lit(True))"
assert repr(Tuple(Val(1), Val(2), Val(3))) == "Tuple(Lit(1), Lit(2), Lit(3))"
```

They evaluate to parenthesised tuples:

<!-- name: test_tuples -->
```python
assert repr(value(Tuple(Val(1), Val(True)))) == "(1, True)"
assert repr(value(Tuple(Val(42), Val("hi"), Val(False)))) == "(42, 'hi', False)"
```

An empty tuple is not allowed:

<!-- name: test_tuples -->
```python
try:
    Tuple()
except ValueError as e:
    assert "at least one element" in str(e)
```

## Element access

Access elements by 0-based integer index with `t[i]`:

<!-- name: test_tuples -->
```python
t = Tuple(Val(10), Val(True), Val("hi"))
assert repr(value(t[0])) == "10"
assert repr(value(t[1])) == "True"
assert repr(value(t[2])) == "'hi'"
```

## Python unpacking

Because `TupleExpr` implements `__iter__`, standard Python unpacking works:

<!-- name: test_tuples -->
```python
t = Tuple(Val(3), Val(False))
a, b = t
assert repr(value(a)) == "3"
assert repr(value(b)) == "False"
```

This is particularly convenient when deconstructing tuples returned from set
operations or function calls inside a spec.

## Functional update

`replace(index, new_value)` returns a new tuple with one element changed,
leaving all others intact:

<!-- name: test_tuples -->
```python
t = Tuple(Val(1), Val(2), Val(3))
t2 = t.replace(1, Val(99))
assert repr(value(t2)) == "(1, 99, 3)"
assert repr(value(t))  == "(1, 2, 3)"   # original unchanged
```

## Cartesian products — `AllTuples`

`AllTuples(S1, S2, ...)` creates the set of all tuples with the first element
from `S1`, the second from `S2`, and so on. See [sets.md](sets.md) for
details and examples.

## Tuple variables

`Var("name", tuple[int, bool])` creates a symbolic variable with the
appropriate `TupleSort`:

<!-- name: test_tuples -->
```python
t = Var("pair", tuple[int, bool])
assert repr(t[0]) == "TupleGet(Var('pair', TupleSort(IntSort(), BoolSort())), 0)"
```
