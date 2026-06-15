# Union expressions

Union types (also called sum types or tagged variants) let a single
expression hold one of several distinct alternatives. Each alternative has a
**tag** (a string name) and an optional **payload** value.

Before we start, let's import the necessary classes from our library:

<!-- name: test_unions -->
```python
from wunderspec import *
```

## Defining a union type — `@union`

Decorate a class with `@union`. Each annotated field defines one variant:
the field name becomes the tag, and the `Variant[...]` type argument determines
the payload sort. Use `Variant[Unit]` for a no-payload variant:

<!-- name: test_unions -->
```python
@union
class Option:
    Some: Variant[int]    # carries an int payload
    None_: Variant[Unit]  # no payload
```

<!-- name: test_unions -->
```python
@union
class Result:
    Ok: Variant[int]
    Err: Variant[str]
```

## Constructing a variant

Call the variant as a classmethod. For payload variants pass the value; for
no-payload variants call with no arguments:

<!-- name: test_unions -->
```python
x = Option.Some(42)
y = Option.None_()
assert repr(value(x)) == "Some(42)"
assert repr(value(y)) == "None_"
```

Auto-coercion of Python literals applies to payload arguments:

<!-- name: test_unions -->
```python
assert repr(value(Option.Some(100)))   == "Some(100)"
assert repr(value(Result.Ok(7)))       == "Ok(7)"
assert repr(value(Result.Err("oops"))) == "Err('oops')"
```

## Accessing the tag

`.tag` returns the variant tag as a `StrExpr`. This is useful when storing
union values in a map and needing to discriminate later:

<!-- name: test_unions -->
```python
x = Option.Some(42)
assert repr(value(x.tag)) == "'Some'"

y = Option.None_()
assert repr(value(y.tag)) == "'None_'"
```

## Pattern matching — `.match()`

`.match(**cases)` dispatches on the variant tag. Each keyword argument is
the tag name mapped to a callable:

- For variants **with payload**: the callable takes one argument (the payload
  expression).
- For variants **without payload**: the callable takes no arguments.

All cases must return expressions of the **same sort**. The match must be
exhaustive (all tags covered), unless a `default` is provided:

<!-- name: test_unions -->
```python
x = Option.Some(42)
result = x.match(
    Some=lambda v: v + Val(1),
    None_=lambda: Val(0),
)
assert repr(value(result)) == "43"
```

<!-- name: test_unions -->
```python
y = Option.None_()
result = y.match(
    Some=lambda v: v + Val(1),
    None_=lambda: Val(0),
)
assert repr(value(result)) == "0"
```

## Non-exhaustive match with `default`

Pass `default=` to handle any unspecified variants. It can be a zero-argument
callable, an `Expr`, or a literal:

<!-- name: test_unions -->
```python
r = Result.Err("oops")
result = r.match(
    Ok=lambda v: v + Val(1),
    default=Val(-1),          # handles Err
)
assert repr(value(result)) == "-1"
```

<!-- name: test_unions -->
```python
# callable default:
result2 = r.match(
    Ok=lambda v: v + Val(1),
    default=lambda: Val(-1),
)
assert repr(value(result2)) == "-1"
```

## Error cases

Specifying an unknown tag or omitting a tag without a default raises:

<!-- name: test_unions -->
```python
try:
    Option.Some(1).match(Some=lambda v: v, Typo=lambda: Val(0))
except ValueError as e:
    assert "Unknown variants" in str(e)
```

<!-- name: test_unions -->
```python
try:
    Option.Some(1).match(Some=lambda v: v)   # None_ not covered
except ValueError as e:
    assert "Non-exhaustive match" in str(e)
```

## Union variables

`Var("name", MyUnionClass)` creates a symbolic variable of the union type:

<!-- name: test_unions -->
```python
v = Var("opt", Option)
assert isinstance(v, UnionExpr)
```
