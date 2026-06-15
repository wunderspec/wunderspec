# Integer expressions

Integer expressions are very similar to those in Python. As in Python and
TLA<sup>+</sup>, integers in Wunderspec are unbounded.

Before we start, let's import the necessary classes from our library:

<!-- name: test_integers -->
```python
from wunderspec import *
```

## Integer literals

Like in Python, we create integer literals by simply writing the number:

<!-- name: test_integers -->
```python
assert repr(Val(42)) == "Lit(42)"
assert repr(Val(-7)) == "Lit(-7)"
assert repr(Val(42_000_000)) == "Lit(42000000)"
```

## Integer arithmetic

As you would expect, we have the basic arithmetic operations:

<!-- name: test_integers -->
```python
assert repr(Val(2) + 3) == "ADD(Lit(2), Lit(3))"
assert repr(Val(5) - 3) == "SUB(Lit(5), Lit(3))"
assert repr(Val(4) * 6) == "MUL(Lit(4), Lit(6))"
assert repr(Val(8) / 2) == "DIV(Lit(8), Lit(2))"
assert repr(Val(10) % 3) == "MOD(Lit(10), Lit(3))"
```

Evaluating these expressions gives us the expected results:

<!-- name: test_integers -->
```python
assert repr(value(Val(2) + 3)) == "5"
assert repr(value(Val(5) - 3)) == "2"
assert repr(value(Val(4) * 6)) == "24"
assert repr(value(Val(9) / 2)) == "4"
assert repr(value(Val(10) % 3)) == "1"
```

Note, however, that division `/` performs integer (floor) division, like the
operator `//` in Python. We should be also careful about the behavior of the the
integer division `/` and modulo operator `%` over negative numbers, which
follows the Python convention.  This behavior is also consistent with how the
model checker [TLC][] handles integer arithmetic, see [this issue][modulo] for
more details.

Here are examples of integer and modulo division:

<!-- name: test_integers -->
```python
assert repr(value(Val(100) / 3)) == "33"
assert repr(value(Val(-100) / 3)) == "-34"
assert repr(value(Val(100) / (-3))) == "-34"
assert repr(value(Val(-100) / (-3))) == "33"

assert repr(value(Val(100) % 3)) == "1"
assert repr(value(Val(-100) % 3)) == "2"
assert repr(value(Val(100) % (-3))) == "-2"
assert repr(value(Val(-100) % (-3))) == "-1"
```

## Exponentiation

Similar to Python and TLA<sup>+</sup>, Wunderspec supports exponentiation using the `**` operator:

<!-- name: test_integers -->
```python
assert repr(Val(3) ** 4) == "POW(Lit(3), Lit(4))"
assert repr(Val(3) ** 0) == "POW(Lit(3), Lit(0))"
```

As you would expect, evaluating these expressions gives us:

<!-- name: test_integers -->
```python
assert repr(value(Val(3) ** 4)) == "81"
assert repr(value(Val(3) ** 0)) == "1"
```

However, note that exponentiation with negative exponents is not supported, as
it would lead to non-integer results:

<!-- name: test_integers -->
```python
try:
    value(Val(3) ** (-6))
except Exception as e:
    assert str(e) == "Negative exponents are not supported"
```

The same happens for `Val(0) ** 0`:

<!-- name: test_integers -->
```python
try:
    value(Val(0) ** (0))
except Exception as e:
    assert str(e) == "0**0 is undefined"
```

## Integer comparisons

Integer expressions can be compared using the standard comparison operators:

<!-- name: test_integers -->
```python
assert repr(Val(5) < 10) == "LT(Lit(5), Lit(10))"
assert repr(Val(5) <= 5) == "LE(Lit(5), Lit(5))"
assert repr(Val(7) > 3) == "GT(Lit(7), Lit(3))"
assert repr(Val(7) >= 8) == "GE(Lit(7), Lit(8))"
assert repr(Val(4) == 4) == "EQ(Lit(4), Lit(4))"
assert repr(Val(4) != 5) == "NE(Lit(4), Lit(5))"
```

They evaluate as expected:

<!-- name: test_integers -->
```python
assert repr(value(Val(5) < 10)) == "True"
assert repr(value(Val(5) <= 5)) == "True"
assert repr(value(Val(7) > 3)) == "True"
assert repr(value(Val(7) >= 8)) == "False"
assert repr(value(Val(4) == 4)) == "True"
assert repr(value(Val(4) != 5)) == "True"
```

## Conditional expressions

Similar to the Boolean case, we can write conditional expressions:

<!-- name: test_integers -->
```python
cond_expr = Val(1).if_(Val(3) > 2).else_(Val(-1))
assert repr(cond_expr) == "Ite(GT(Lit(3), Lit(2)), Lit(1), Lit(-1))"
```

The branches auto-coerce raw integers, so the else branch can be a plain `int`:

<!-- name: test_integers -->
```python
cond_expr = Val(1).if_(Val(3) > 2).else_(-1)
assert repr(cond_expr) == "Ite(GT(Lit(3), Lit(2)), Lit(1), Lit(-1))"
```


[modulo]: https://github.com/apalache-mc/apalache/issues/331
[TLC]: https://lamport.azurewebsites.net/tla/tools.html?unhideBut=hide-tlc&unhideDiv=tlc