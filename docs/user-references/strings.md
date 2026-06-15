# String expressions

Strings in Wunderspec are symbolic constants useful as labels, message tags,
and map keys. The string sort does **not** support concatenation or indexing
at the expression level — strings are opaque values that can only be compared
for equality.

Before we start, let's import the necessary classes from our library:

<!-- name: test_strings -->
```python
from wunderspec import *
```

## String literals

`Val("...")` wraps a Python string in a Wunderspec literal:

<!-- name: test_strings -->
```python
assert repr(Val("hello"))  == "Lit('hello')"
assert repr(Val("world"))  == "Lit('world')"
assert repr(Val(""))       == "Lit('')"
```

Evaluating a string literal returns the string wrapped in a `StrValue`:

<!-- name: test_strings -->
```python
assert repr(value(Val("hello"))) == "'hello'"
```

## String variables

`Var("name", str)` creates a symbolic string variable:

<!-- name: test_strings -->
```python
s = Var("label", str)
assert repr(s) == "Var('label', StrSort())"
```

## Equality and inequality

Strings support `==` and `!=`:

<!-- name: test_strings -->
```python
assert repr(Val("a") == Val("a")) == "EQ(Lit('a'), Lit('a'))"
assert repr(Val("a") != Val("b")) == "NE(Lit('a'), Lit('b'))"
```

They evaluate as expected:

<!-- name: test_strings -->
```python
assert repr(value(Val("hello") == Val("hello"))) == "True"
assert repr(value(Val("hello") == Val("world"))) == "False"
assert repr(value(Val("a") != Val("b")))         == "True"
```

## Strings in sets and maps

Strings can be used as set elements or map keys:

<!-- name: test_strings -->
```python
labels = Set("a", "b", "c")
assert repr(value(labels.size))               == "3"
assert repr(value(labels.contains("a")))      == "True"
assert repr(value(labels.contains("z")))      == "False"
```

<!-- name: test_strings -->
```python
m = Map(str, int).insert("x", 1).insert("y", 2)
assert repr(value(m[Val("x")])) == "1"
assert repr(value(m.size))      == "2"
```

## What is not supported

- String concatenation (`+`) — use `Enum` or records for structured labels
- String length or indexing
- Pattern matching on string content

For structured variant labels, see [unions.md](unions.md).
For enum-valued constants, see your Python `enum.Enum` type together with `Val`.
