# Quantifiers and set comprehensions

Wunderspec provides both a **generator-expression style** and a **method
style** for building quantified formulas and set transformations.  The two
styles are interchangeable — choose whichever reads more naturally in your
spec.

Before we start, let's import the necessary classes from our library:

<!-- name: test_quantifiers -->
```python
from wunderspec import *
```

## Universal quantification — `Forall`

`Forall(P(x) for x in S)` builds the formula `∀ x ∈ S : P(x)`:

<!-- name: test_quantifiers -->
```python
S = Set(2, 4, 6)
assert repr(value(Forall(x % Val(2) == Val(0) for x in S))) == "True"
assert repr(value(Forall(x > Val(10)          for x in S))) == "False"
```

The method form is equivalent:

<!-- name: test_quantifiers -->
```python
assert repr(value(S.forall(lambda x: x % Val(2) == Val(0)))) == "True"
```

## Existential quantification — `Exists`

`Exists(P(x) for x in S)` builds `∃ x ∈ S : P(x)`:

<!-- name: test_quantifiers -->
```python
S = Set(1, 2, 3)
assert repr(value(Exists(x > Val(2) for x in S))) == "True"
assert repr(value(Exists(x > Val(9) for x in S))) == "False"
```

Method form:

<!-- name: test_quantifiers -->
```python
assert repr(value(S.exists(lambda x: x > Val(2)))) == "True"
```

## Multiple binders

Both `Forall` and `Exists` accept multiple `for` clauses, ranging over the
Cartesian product of the given sets:

<!-- name: test_quantifiers -->
```python
S1 = Set(1, 2)
S2 = Set(3, 4)
# ∀ x ∈ S1, y ∈ S2 : x + y > 3
assert repr(value(Forall(x + y > Val(3) for x in S1 for y in S2))) == "True"
# ∃ x ∈ S1, y ∈ S2 : x + y == 5
assert repr(value(Exists(x + y == Val(5) for x in S1 for y in S2))) == "True"
```

## Quantifying over lists

`Forall` and `Exists` (and the matching `.forall`/`.exists` methods) also range
over **lists**.  Iterating a list binds the loop variable to each element in
order; internally this quantifies over the list's index set:

<!-- name: test_quantifiers -->
```python
lst = List(2, 4, 6)
assert repr(value(Forall(x % Val(2) == Val(0) for x in lst))) == "True"
assert repr(value(Exists(x > Val(5) for x in lst)))           == "True"
assert repr(value(lst.forall(lambda x: x > Val(0))))          == "True"
```

See [lists.md](lists.md#quantifiers) for more on list quantification.

## Filtered set comprehension — `SetIf`

`SetIf(P(x) for x in S)` produces `{ x ∈ S : P(x) }`:

<!-- name: test_quantifiers -->
```python
S = Set(1, 2, 3, 4, 5)
evens = SetIf(x % Val(2) == Val(0) for x in S)
assert repr(value(evens.size))        == "2"
assert repr(value(evens.contains(4))) == "True"
assert repr(value(evens.contains(3))) == "False"
```

The method form `.filter(predicate)` on `SetExpr` is identical:

<!-- name: test_quantifiers -->
```python
evens2 = S.filter(lambda x: x % Val(2) == Val(0))
assert repr(value(evens2.size)) == "2"
```

## Image set — `Set` (generator form)

`Set(f(x) for x in S)` produces `{ f(x) : x ∈ S }` as a `SetExpr`:

<!-- name: test_quantifiers -->
```python
S = Set(1, 2, 3)
doubled = Set(x * Val(2) for x in S)
assert repr(value(doubled.size))        == "3"
assert repr(value(doubled.contains(6))) == "True"
```

Method form:

<!-- name: test_quantifiers -->
```python
doubled2 = S.map(lambda x: x * Val(2))
assert repr(value(doubled2.size)) == "3"
```

## Function-as-map — `Map` (generator form)

`Map(f(x) for x in domain)` produces a `MapExpr` — a total function from
`domain` to values of `f`. It corresponds to `[x ∈ S ↦ f(x)]` in
TLA<sup>+</sup>. Exactly one `for` clause is required:

<!-- name: test_quantifiers -->
```python
domain = Set(1, 2, 3)
squares = Map(x * x for x in domain)
assert repr(value(squares))         == "Map(1 -> 1, 2 -> 4, 3 -> 9)"
assert repr(value(squares[Val(2)])) == "4"
```

The method form `.map_to(mapper)` on `SetExpr` is identical:

<!-- name: test_quantifiers -->
```python
squares2 = domain.map_to(lambda x: x * x)
assert repr(value(squares2[Val(3)])) == "9"
```

## Temporal quantification

When the predicate body is a `TemporalExpr`, `Forall` and `Exists` lift the
result to `TemporalSort` automatically. This is useful for expressing
fairness conditions over a set of processes:

```python
# Conceptual example (requires a state machine context):
# Always(Forall(WeakFair(send, p) for p in PROCS))
```

See [temporal.md](temporal.md) for details on temporal operators.
