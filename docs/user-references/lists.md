# List expressions

Lists are ordered, finite sequences with 0-based indexing. Like all
Wunderspec data structures they are **immutable** — every operation that
modifies a list returns a new one.

Before we start, let's import the necessary classes from our library:

<!-- name: test_lists -->
```python
from wunderspec import *
```

## Creating a list

Pass elements directly (auto-coercion applies):

<!-- name: test_lists -->
```python
assert repr(List(1, 2, 3))             == "List(Lit(1), Lit(2), Lit(3))"
assert repr(List(True, False, True))   == "List(Lit(True), Lit(False), Lit(True))"
assert repr(List('a', 'b', 'c'))       == "List(Lit('a'), Lit('b'), Lit('c'))"
```

Create an empty list by specifying the element type:

<!-- name: test_lists -->
```python
assert repr(List(int))  == "List(IntSort())"
assert repr(List(bool)) == "List(BoolSort())"
```

All of the above evaluate as expected:

<!-- name: test_lists -->
```python
assert repr(value(List(1, 2, 3)))           == "[1, 2, 3]"
assert repr(value(List(True, False, True)))  == "[True, False, True]"
assert repr(value(List(int)))               == "[]"
```

## Integer ranges

`Range(lower, upper)` creates the list `[lower, lower+1, …, upper-1]`
(upper-exclusive, matching Python's `range`):

<!-- name: test_lists -->
```python
assert repr(Range(0, 5)) == "Range(Lit(0), Lit(5))"
assert repr(value(Range(0, 5))) == "[0, 1, 2, 3, 4]"
assert repr(value(Range(3, 3))) == "[]"
```

## Element access and slicing

Access an element by index with `lst[index]`:

<!-- name: test_lists -->
```python
lst = List(10, 20, 30)
assert repr(value(lst[0])) == "10"
assert repr(value(lst[2])) == "30"
```

Slice with `lst[start:end]` (end-exclusive):

<!-- name: test_lists -->
```python
lst = List(1, 2, 3, 4, 5)
assert repr(value(lst[1:3])) == "[2, 3]"
assert repr(value(lst[:2]))  == "[1, 2]"
assert repr(value(lst[3:]))  == "[4, 5]"
```

## Adding an element and updating

There is no `append` method: lists are immutable, so there is nothing to append
*to*, and a method named `append` would be easily confused with Python's
in-place `list.append`. Instead, add a single element at the end by concatenating
a one-element list with `+`:

<!-- name: test_lists -->
```python
lst = List(1, 2, 3)
lst2 = lst + List(Val(4))
assert repr(value(lst2)) == "[1, 2, 3, 4]"
assert repr(value(lst))  == "[1, 2, 3]"   # original unchanged
```

The single-element case `lst + List(e)` is recognized by the backends and
rendered as the idiomatic `Append(lst, e)` in TLA+ (and an efficient append in
the Rust backend), so you pay nothing for the explicit form.

`replace(index, value)` returns a new list with the element at `index`
replaced:

<!-- name: test_lists -->
```python
lst = List(1, 2, 3)
lst2 = lst.replace(1, Val(99))
assert repr(value(lst2)) == "[1, 99, 3]"
```

## Concatenation

The `+` operator concatenates two lists of the same element type:

<!-- name: test_lists -->
```python
a = List(1, 2)
b = List(3, 4)
assert repr(value(a + b)) == "[1, 2, 3, 4]"
```

## Size and index set

`lst.size` returns the number of elements; `lst.keys` returns the set of
valid indices `{0, 1, …, size-1}`:

<!-- name: test_lists -->
```python
lst = List(10, 20, 30)
assert repr(value(lst.size)) == "3"
assert repr(value(lst.keys)) == "Set({0, 1, 2})"
```

## Emptiness check

`.is_empty` returns a `BoolExpr` that is `True` when the list has no elements:

<!-- name: test_lists -->
```python
assert repr(value(List(int).is_empty))    == "True"
assert repr(value(List(1, 2, 3).is_empty)) == "False"
```

## Filtering

`filter(predicate)` returns a new list containing only the elements for which
the predicate holds, preserving order:

<!-- name: test_lists -->
```python
lst = List(1, 2, 3, 4, 5)
big = lst.filter(lambda x: x > Val(2))
assert repr(value(big)) == "[3, 4, 5]"
```

## Reduction

`reduce(function, initial)` folds a binary function over the list **in
order**, starting from `initial`:

<!-- name: test_lists -->
```python
lst = List(1, 2, 3, 4, 5)
total = lst.reduce(lambda acc, x: acc + x, 0)
assert repr(value(total)) == "15"
```

The initial value is auto-coerced, so a raw `0` works just like `Val(0)`.

Unlike set reduce, list reduce is sensitive to element order, so the
operation does not need to be commutative.

## Quantifiers

`forall` and `exists` express universal and existential quantification over the
**elements** of a list. They desugar to quantification over the list's index
set (`l.keys`): `l.forall(lambda x: P(x))` is `l.keys.forall(lambda i: P(l[i]))`.

<!-- name: test_lists -->
```python
lst = List(2, 4, 6)
assert repr(value(lst.forall(lambda x: x % Val(2) == Val(0)))) == "True"
assert repr(value(lst.exists(lambda x: x > Val(5))))           == "True"
```

The standalone `Forall` and `Exists` constructors also range over lists when
given a list in their generator expression, binding the loop variable to each
element in order:

<!-- name: test_lists -->
```python
lst = List(2, 4, 6)
assert repr(value(Forall(x % Val(2) == Val(0) for x in lst))) == "True"
assert repr(value(Exists(x > Val(5) for x in lst)))           == "True"
```

Quantification over an empty list follows the usual convention: `forall` is
vacuously `True` and `exists` is `False`.

<!-- name: test_lists -->
```python
empty = List(int)
assert repr(value(empty.forall(lambda x: x > Val(0)))) == "True"
assert repr(value(empty.exists(lambda x: x > Val(0)))) == "False"
```
