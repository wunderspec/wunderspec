# Set expressions

Sets are cornerstone of TLA<sup>+</sup>, and thus, they are also a fundamental part of
Wunderspec. There are several important distinctions of sets in Wunderspec
compared to Python, mainly due to the influence of TLA<sup>+</sup>:

 1. **Immutable sets**. As in TLA<sup>+</sup>, sets are immutable. Once a set is
 created, its elements cannot be changed. This is similar to `frozenset` in Python.
 
 1. **Richer data structures**. Since sets and other data structures in
 TLA<sup>+</sup>/Wunderspec are immutable, they can be nested within each other
 without restrictions.

 1. **Infinite sets**. As in TLA<sup>+</sup>, Wunderspec supports infinite sets
 such as the set of all integers `Int` or the set of all natural numbers `Nat`.
 There are obvious limitations on what the Wunderspec interpreter can do with
 infinite sets, but they can be used in specifications, symbolic model checking,
 and theorem proving.
 
Before we start, let's import the necessary classes from our library:

<!-- name: test_sets -->
```python
from wunderspec import *
```

## Set construction

The simplest way to create a finite set is to simply list its elements using the
`Set` constructor:

<!-- name: test_sets -->
```python
assert repr(Set(1, 2, 3)) == "Set(Lit(1), Lit(2), Lit(3))"
assert repr(Set(Set(1, 2), Set(2, 3))) == "Set(Set(Lit(1), Lit(2)), Set(Lit(2), Lit(3)))"
```

Importantly, when you construct an empty set, you have to specify the element type:

<!-- name: test_sets -->
```python
assert repr(Set(IntSort())) == "Set(IntSort())"
```

The interpreter evaluates all of the above set expressions as expected:

<!-- name: test_sets -->
```python
assert repr(value(Set(1, 2, 3))) == "Set({1, 2, 3})"
expected = "Set({Set({1, 2}), Set({2, 3})})"
assert repr(value(Set(Set(1, 2), Set(2, 3)))) == expected
assert repr(value(Set(IntSort()))) == "Set()"
```

## Integer intervals

Another way is to create an integer interval using the `Interval` constructor:

<!-- name: test_sets -->
```python
assert repr(Interval(10, 20)) == "Interval(Lit(10), Lit(20))"
```

## Set membership

You can check if an element is in a set using the `in_` method, or the
`contains` method:

<!-- name: test_sets -->
```python
s = Set(1, 2, 3)
assert repr(Val(2).in_(s)) == "In(Lit(2), Set(Lit(1), Lit(2), Lit(3)))"
assert repr(s.contains(2)) == "In(Lit(2), Set(Lit(1), Lit(2), Lit(3)))"
assert repr(s.contains(Val(2))) == "In(Lit(2), Set(Lit(1), Lit(2), Lit(3)))"
```

These expressions evaluate as expected:

<!-- name: test_sets -->
```python
assert repr(value(Val(2).in_(s))) == "True"
assert repr(value(s.contains(2))) == "True"
assert repr(value(s.contains(Val(2)))) == "True"
```

Notice that it is incorrect to use Python's `in` operator for set membership, as
it would need an iteration over the set, which is not computed at this point:

<!-- name: test_sets -->
```python
try:
    2 in s      # Don't do this!
except TypeError as e:
    print(e)    # Outputs: 'Set' object is not iterable
```

## Set inclusion

Sets can be compared for inclusion using the method `issubset`:

<!-- name: test_sets -->
```python
s12 = Val(1).upto(2)
s123 = Val(1).upto(3)
assert repr(s12.issubset(s123)) == "SUBSETEQ(Interval(Lit(1), Lit(2)), Interval(Lit(1), Lit(3)))"
assert repr(s123.issubset(s123)) == "SUBSETEQ(Interval(Lit(1), Lit(3)), Interval(Lit(1), Lit(3)))"
assert repr(s123.issubset(s12)) == "SUBSETEQ(Interval(Lit(1), Lit(3)), Interval(Lit(1), Lit(2)))"
```

They evaluate as expected:

<!-- name: test_sets -->
```python
assert repr(value(s12.issubset(s123))) == "True"
assert repr(value(s123.issubset(s123))) == "True"
assert repr(value(s123.issubset(s12))) == "False"
```

For convenience, you can also use the operators `<=` and `<` for subset and
proper subset, as well as `>=` and `>` for superset and proper superset. They
are all immediately translated to expressions over `SubsetEq`, `And`, and `Not`:

<!-- name: test_sets -->
```python
assert repr(s12 <= s123) == "SUBSETEQ(Interval(Lit(1), Lit(2)), Interval(Lit(1), Lit(3)))"
assert repr(s12 < s123) == "AND(SUBSETEQ(Interval(Lit(1), Lit(2)), Interval(Lit(1), Lit(3))), NOT(EQ(Interval(Lit(1), Lit(2)), Interval(Lit(1), Lit(3)))))"
assert repr(s12 > s123) == "AND(SUBSETEQ(Interval(Lit(1), Lit(3)), Interval(Lit(1), Lit(2))), NOT(EQ(Interval(Lit(1), Lit(2)), Interval(Lit(1), Lit(3)))))"
assert repr(s12 >= s123) == "SUBSETEQ(Interval(Lit(1), Lit(3)), Interval(Lit(1), Lit(2)))"
```

## Set algebra

As expected, Wunderspec supports standard set operations like union,
intersection, and set difference:

<!-- name: test_sets -->
```python
s1 = Set(1, 2)
s2 = Set(2, 3)
assert repr(s1.union(s2)) == "UNION(Set(Lit(1), Lit(2)), Set(Lit(2), Lit(3)))"
assert repr(s1.intersect(s2)) == "INTERSECT(Set(Lit(1), Lit(2)), Set(Lit(2), Lit(3)))"
assert repr(s1.difference(s2)) == "DIFFERENCE(Set(Lit(1), Lit(2)), Set(Lit(2), Lit(3)))"
```

Their evaluations are as expected:

<!-- name: test_sets -->
```python
assert repr(value(s1.union(s2))) == "Set({1, 2, 3})"
assert repr(value(s1.intersect(s2))) == "Set({2})"
assert repr(value(s1.difference(s2))) == "Set({1})"
```

Similar to Python sets, Wunderspec also supports the `|`, `&`, and `-` operators for union, intersection, and difference, respectively:

<!-- name: test_sets -->
```python
assert repr(s1 | s2) == repr(s1.union(s2))
assert repr(s1 & s2) == repr(s1.intersect(s2))
assert repr(s1 - s2) == repr(s1.difference(s2))
```

## Emptiness check

`.is_empty` returns a `BoolExpr` that is `True` when the set has no elements:

<!-- name: test_sets -->
```python
assert repr(value(Set(int).is_empty))     == "True"
assert repr(value(Set(1, 2, 3).is_empty)) == "False"
```

It is equivalent to `s.size == Val(0)` but more readable, and is the
preferred way to guard against operating on an empty set:

<!-- name: test_sets -->
```python
s = Set(1, 2, 3)
assert repr(value(~s.is_empty)) == "True"   # ~  is logical Not
```

## Cardinality

The number of elements in a finite set is obtained via the `.size` property:

<!-- name: test_sets -->
```python
s = Set(10, 20, 30)
assert repr(s.size) == "CARDINALITY(Set(Lit(10), Lit(20), Lit(30)))"
assert repr(value(s.size)) == "3"
```

## Infinite sets

Wunderspec provides two built-in infinite sets:

<!-- name: test_sets -->
```python
assert repr(Ints) == "Ints"
assert repr(UnsignedInts) == "UnsignedInts"
```

These can be used in specifications and symbolic model checking. Directly
evaluating them in the interpreter raises an error, since the interpreter
cannot enumerate an infinite set.

## Filtering

`filter` creates the subset of elements that satisfy a predicate — the
set-builder notation `{ x ∈ S : P(x) }` from TLA<sup>+</sup>:

<!-- name: test_sets -->
```python
s = Set(1, 2, 3, 4, 5)
evens = s.filter(lambda x: x % Val(2) == Val(0))
# Filtered sets are lazily evaluated; use set operations rather than
# inspecting the repr directly:
assert repr(value(evens.size)) == "2"
assert repr(value(evens.contains(2))) == "True"
assert repr(value(evens.contains(3))) == "False"
```

The generator form `SetIf(P(x) for x in S)` is equivalent and sometimes
reads more naturally:

<!-- name: test_sets -->
```python
evens2 = SetIf(x % Val(2) == Val(0) for x in Set(1, 2, 3, 4, 5))
assert repr(value(evens2.size)) == "2"
assert repr(value(evens2.contains(4))) == "True"
```

## Mapping over a set

`map` creates the image of a set under a function — `{ f(x) : x ∈ S }`:

<!-- name: test_sets -->
```python
s = Set(1, 2, 3)
doubled = s.map(lambda x: x * Val(2))
# Mapped sets are lazily evaluated; use operations on the result:
assert repr(value(doubled.size)) == "3"
assert repr(value(doubled.contains(4))) == "True"
assert repr(value(doubled.contains(1))) == "False"
```

The generator form `Set(f(x) for x in S)` is equivalent:

<!-- name: test_sets -->
```python
doubled2 = Set(x * Val(2) for x in Set(1, 2, 3))
assert repr(value(doubled2.size)) == "3"
assert repr(value(doubled2.contains(6))) == "True"
```

## Quantifiers

`forall` and `exists` express universal and existential quantification over a
set:

<!-- name: test_sets -->
```python
s = Set(2, 4, 6)
assert repr(value(s.forall(lambda x: x % Val(2) == Val(0)))) == "True"
assert repr(value(s.exists(lambda x: x > Val(5)))) == "True"
```

The standalone `Forall` and `Exists` constructors accept generator expressions
and support multiple binders:

<!-- name: test_sets -->
```python
s1 = Set(1, 2)
s2 = Set(3, 4)
result = Forall(x + y > Val(3) for x in s1 for y in s2)
assert repr(value(result)) == "True"
```

## Reduction

`reduce` folds a binary function over all elements of a set with an initial
accumulator value:

<!-- name: test_sets -->
```python
s = Set(1, 2, 3, 4)
total = s.reduce(lambda acc, x: acc + x, 0)
assert repr(value(total)) == "10"
```

The initial value is auto-coerced, so a raw `0` works just like `Val(0)`.

Note that sets are unordered, so `reduce` is only predictable when the
operation is commutative and associative (e.g., addition or multiplication).

## Choosing an element

`choose` selects an element satisfying a predicate — corresponding to
TLA<sup>+</sup>'s `CHOOSE` operator. When exactly one element matches, the
result is unambiguous:

<!-- name: test_sets -->
```python
s = Set(1, 2, 3)
picked = s.choose(lambda x: x == Val(2))
assert repr(value(picked)) == "2"
```

When multiple elements match the interpreter picks deterministically, but
specifications should not rely on which one is chosen. Choosing from a set
where no element matches raises a `ValueError` at evaluation time.

## Flattening a set of sets

`.flattened` computes the generalised union `⋃ SS` of a set of sets:

<!-- name: test_sets -->
```python
ss = Set(Set(1, 2), Set(2, 3), Set(3, 4))
assert repr(value(ss.flattened)) == "Set({1, 2, 3, 4})"
```

## Higher-order set constructors

### Power set — `AllSubsets`

`AllSubsets(S)` produces the set of all subsets of `S` (the power set `SUBSET
S` in TLA<sup>+</sup>). Like filtered and mapped sets, it evaluates lazily:

<!-- name: test_sets -->
```python
ps = AllSubsets(Set(1, 2))
assert repr(value(ps.size)) == "4"          # 2^2 subsets
assert repr(value(ps.contains(Set(1)))) == "True"
assert repr(value(ps.contains(Set(1, 2)))) == "True"
```

### Function sets — `AllMaps`

`AllMaps(keys, values)` produces the set of all total maps (functions) from
`keys` to `values` — `[keys -> values]` in TLA<sup>+</sup>:

<!-- name: test_sets -->
```python
fm = AllMaps(Set(1, 2), Set(True, False))
assert repr(value(fm.size)) == "4"
```

### Cartesian product — `AllTuples`

`AllTuples(S1, S2, ...)` produces the Cartesian product `S1 × S2 × …`:

<!-- name: test_sets -->
```python
cp = AllTuples(Set(1, 2), Set(True, False))
assert repr(value(cp.size)) == "4"
```

### Record sets — `AllRecords`

`AllRecords(field=S, ...)` produces the set of all records where each field
independently ranges over its given set — `[field1: S1, field2: S2]` in
TLA<sup>+</sup>:

<!-- name: test_sets -->
```python
rs = AllRecords(x=Set(1, 2), y=Set(True, False))
assert repr(value(rs.size)) == "4"
```
