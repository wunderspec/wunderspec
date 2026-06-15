# Record expressions

Records group named fields of potentially different sorts into a single
value. They correspond to TLA<sup>+</sup> records. Like all Wunderspec values,
records are **immutable** — updates return a new record.

Before we start, let's import the necessary classes from our library:

<!-- name: test_records -->
```python
from typing import Annotated
from wunderspec import *
```

## Anonymous records — `Record`

`Record(**fields)` creates a record from keyword arguments. Fields are
stored in alphabetical order regardless of the declaration order:

<!-- name: test_records -->
```python
r = Record(name=Val("Alice"), age=Val(30))
assert repr(r)         == "Record(age=Lit(30), name=Lit('Alice'))"
assert repr(value(r))  == "Record(age=30, name='Alice')"
```

Wrap literals with `Val()`:

<!-- name: test_records -->
```python
r = Record(x=Val(1), y=Val(True), z=Val("hi"))
assert repr(value(r)) == "Record(x=1, y=True, z='hi')"
```

## Field access

Access fields via dot notation or subscript:

<!-- name: test_records -->
```python
r = Record(name=Val("Alice"), age=Val(30))
assert repr(value(r.name))      == "'Alice'"
assert repr(value(r["age"]))    == "30"
```

Record fields take precedence over expression attributes and methods in dot
notation. If a field is called `name`, `sort`, `node`, `tag`, `replace`, or
another Wunderspec API name, `r.name` still means the field. Use the reserved
`._` namespace to reach the expression API:

<!-- name: test_records -->
```python
r = Record(name=Val("Alice"), replace=Val(1))
assert repr(value(r.name)) == "'Alice'"
assert repr(value(r.replace)) == "1"
assert r._.sort == r.sort
assert repr(value(r._.replace(name=Val("Bob")).name)) == "'Bob'"
```

## Functional update

`replace(**fields)` returns a new record with the specified fields changed:

<!-- name: test_records -->
```python
r = Record(name=Val("Alice"), age=Val(30))
r2 = r.replace(age=Val(31))
assert repr(value(r2)) == "Record(age=31, name='Alice')"
assert repr(value(r))  == "Record(age=30, name='Alice')"   # original unchanged
```

## Nested functional update — `.edit()`

`replace` only works at the top level. When a record field is itself a
map (or another nested structure), `.edit()` lets you update a path deep in
the structure and get back the modified record:

<!-- name: test_records -->
```python
from typing import Annotated
evm = Record(balances=Map(str, int).insert("alice", 100).insert("bob", 50))
upd = evm.edit()
upd.balances["alice"] = Val(80)
upd.balances["bob"]   = Val(70)
evm2 = upd.result
assert repr(value(evm2.balances["alice"])) == "80"
assert repr(value(evm2.balances["bob"]))   == "70"
assert repr(value(evm.balances["alice"]))  == "100"   # original unchanged
```

## Named record types — `@record`

For reuse and type safety, decorate a class with `@record`. Each field is
annotated with `Annotated[Expr, <python-type>]`, where the second argument
determines the field's sort:

<!-- name: test_records -->
```python
@record
class Point:
    x: Annotated[Expr, int]
    y: Annotated[Expr, int]
```

Instantiate it like a constructor — all fields are required and auto-coerced:

<!-- name: test_records -->
```python
p = Point(x=3, y=4)
assert repr(value(p))   == "Record(x=3, y=4)"
assert repr(value(p.x)) == "3"
assert repr(value(p.y)) == "4"
```

The `@record` class validates field names and sorts at construction time:

<!-- name: test_records -->
```python
try:
    Point(x=1)          # missing y
except TypeError as e:
    assert "Missing required fields" in str(e)
```

<!-- name: test_records -->
```python
try:
    Point(x=1, y=2, z=3)   # extra field
except ValueError as e:
    assert "Extra fields" in str(e)
```

## Record variables

`Var("name", PointClass)` creates a symbolic variable of a `@record` type.
Wunderspec derives the `RecordSort` from the class automatically:

<!-- name: test_records -->
```python
pt = Var("pt", Point)
assert repr(pt.x) == "RecordGet(Var('pt', RecordSort(x=IntSort(), y=IntSort())), 'x')"
```

## Record sets — `AllRecords`

`AllRecords(field=S, ...)` is the set of all records with each field drawn
from its corresponding set. See [sets.md](sets.md) for details and examples.
