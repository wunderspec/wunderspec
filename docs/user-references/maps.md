# Map expressions

Maps (also called functions in TLA<sup>+</sup>) associate keys with values.
Like all data structures in Wunderspec, maps are **immutable** — operations
that "update" a map return a new map, leaving the original unchanged.

Before we start, let's import the necessary classes from our library:

<!-- name: test_maps -->
```python
from wunderspec import *
```

## Creating an empty map

`Map(key_type, value_type)` creates an empty map. You can pass Python types
or sort objects:

<!-- name: test_maps -->
```python
assert repr(Map(int, str))   == "Map(IntSort(), StrSort())"
assert repr(Map(int, bool))  == "Map(IntSort(), BoolSort())"
assert repr(Map(str, int))   == "Map(StrSort(), IntSort())"
```

An empty map evaluates to `Map()`:

<!-- name: test_maps -->
```python
assert repr(value(Map(int, str))) == "Map()"
```

## Creating a map from explicit key-value pairs

`Map((key, value), ...)` creates a map directly from one or more `(key, value)`
tuples. Key and value sorts are inferred from the first pair; all subsequent
pairs must have matching sorts. Raw Python literals are coerced automatically,
just like in `insert`:

<!-- name: test_maps -->
```python
m = Map((Val("a"), Val(1)), (Val("b"), Val(2)))
assert repr(value(m)) == "Map('a' -> 1, 'b' -> 2)"
```

```python
m = Map(("x", 10), ("y", 20))   # literals coerced automatically
assert repr(value(m)) == "Map('x' -> 10, 'y' -> 20)"
```

A single-pair map is also valid:

```python
m = Map((Val(42), Val(True)))
assert repr(value(m)) == "Map(42 -> True)"
```

In TLA<sup>+</sup>, explicit maps translate to the TLC function-merge syntax,
and `EXTENDS TLC` is added to the module automatically:

```tla
("a" :> 1) @@ ("b" :> 2)
```

## Map lookup

Use subscript notation `m[key]` to look up a value. The result is an
expression whose sort matches the map's value sort:

<!-- name: test_maps -->
```python
m = Map(int, int).insert(Val(1), Val(10)).insert(Val(2), Val(20))
assert repr(value(m[Val(1)])) == "10"
assert repr(value(m[Val(2)])) == "20"
```

## Inserting and updating entries

`insert(key, value)` returns a new map with the entry added or replaced. If
the key already exists its value is overwritten; if it does not exist a new
entry is added:

<!-- name: test_maps -->
```python
m = Map(int, int)
m1 = m.insert(Val(1), Val(10))
m2 = m1.insert(Val(2), Val(20))
assert repr(value(m2)) == "Map(1 -> 10, 2 -> 20)"
# Original maps are unchanged:
assert repr(value(m))  == "Map()"
assert repr(value(m1)) == "Map(1 -> 10)"
```

Auto-coercion of Python literals is supported:

<!-- name: test_maps -->
```python
m = Map(int, int).insert(1, 10).insert(2, 20)
assert repr(value(m)) == "Map(1 -> 10, 2 -> 20)"
```

`replace(key, value)` is like `insert` but is only defined for existing keys
— if the key is absent, tools (type checkers, the model checker) may report
an error. Use it to signal intent that the key must already be present:

<!-- name: test_maps -->
```python
m = Map(int, str).insert(1, 'a').insert(2, 'b')
m2 = m.replace(1, 'x')
assert repr(value(m2)) == "Map(1 -> 'x', 2 -> 'b')"
```

## Key set and size

`m.keys` returns the set of keys; `m.size` returns the number of entries:

<!-- name: test_maps -->
```python
m = Map(int, int).insert(1, 10).insert(2, 20).insert(3, 30)
assert repr(value(m.keys)) == "Set({1, 2, 3})"
assert repr(value(m.size)) == "3"
```

## Value set — `.values`

`m.values` returns the set of values in the map. It desugars to
`m.keys.map(lambda k: m[k])`, so — because the result is a **set** — duplicate
values collapse:

<!-- name: test_maps -->
```python
m = Map(int, int).insert(1, 10).insert(2, 20).insert(3, 30)
assert repr(value(m.values).materialize()) == "Set({10, 20, 30})"

# Duplicate values collapse, since the result is a set:
dup = Map(int, int).insert(1, 7).insert(2, 7).insert(3, 9)
assert repr(value(dup.values).materialize()) == "Set({7, 9})"
```

## Reducing a map — `.reduce()`

`m.reduce(function, initial)` folds over the map's key/value pairs. The
`function` takes `(accumulator, key, value)` and `initial` is the starting
accumulator (a raw Python literal is auto-coerced). It desugars to
`m.keys.reduce(lambda acc, k: function(acc, k, m[k]), initial)`:

<!-- name: test_maps -->
```python
m = Map(int, int).insert(1, 10).insert(2, 20).insert(3, 30)

# Sum of the values:
assert repr(value(m.reduce(lambda acc, k, v: acc + v, 0))) == "60"

# A key-aware fold: sum of key * value = 1*10 + 2*20 + 3*30:
assert repr(value(m.reduce(lambda acc, k, v: acc + k * v, 0))) == "140"
```

## Emptiness check

`.is_empty` returns a `BoolExpr` that is `True` when the map has no entries:

<!-- name: test_maps -->
```python
assert repr(value(Map(int, str).is_empty))                        == "True"
assert repr(value(Map(int, int).insert(1, 10).is_empty))          == "False"
```

## Building a map from a generator — `Map`

`Map(expr for x in domain)` creates a map that associates every element of
`domain` with `expr`. It corresponds to the TLA<sup>+</sup> function
constructor `[x ∈ S ↦ expr]`:

<!-- name: test_maps -->
```python
domain = Set(1, 2, 3)
doubled = Map(x * Val(2) for x in domain)
assert repr(value(doubled)) == "Map(1 -> 2, 2 -> 4, 3 -> 6)"
```

The generator form of `Map` requires exactly one `for` clause.

## Functional updates — `.edit()`

`.edit()` returns an `UpdatesBuilder` for making multiple targeted changes to
a map. Each subscript assignment creates a structurally-updated copy of the
map; `.result` retrieves the final expression:

<!-- name: test_maps -->
```python
m = Map(int, int).insert(1, 10).insert(2, 20).insert(3, 30)
upd = m.edit()
upd[Val(2)] = Val(99)
upd[Val(3)] = Val(0)
m2 = upd.result
assert repr(value(m2)) == "Map(1 -> 10, 2 -> 99, 3 -> 0)"
assert repr(value(m))  == "Map(1 -> 10, 2 -> 20, 3 -> 30)"   # original unchanged
```

By default `edit()` uses `replace_only=False`, so new keys can be inserted.
Pass `replace_only=True` to assert the key must already exist:

<!-- name: test_maps -->
```python
m = Map(int, int).insert(1, 10)
upd = m.edit(replace_only=True)
upd[Val(1)] = Val(99)   # key 1 exists — fine
m2 = upd.result
assert repr(value(m2)) == "Map(1 -> 99)"
```

Inside a state machine action you update map-valued state fields with direct
assignment (`s.field[key] = value`, including nested paths); see
[state-machine.md](state-machine.md). Declare a field as
`StateVar[dict[K, V], UPSERT]` to allow inserting new keys.

## Set of all maps — `AllMaps`

`AllMaps(key_set, value_set)` is the set of all total maps from `key_set` to
`value_set`. See [sets.md](sets.md) for details.

## Differences from Python dicts

| Feature | Python `dict` | Wunderspec `Map` |
|---|---|---|
| Mutability | Mutable | Immutable (functional updates) |
| Out-of-bounds lookup | `KeyError` | Undefined behaviour in the model checker |
| Key ordering | Insertion order (3.7+) | Sorted by key repr in output |
