# Boolean expressions

If you have been using Python or any other programming language, you know
Boolean expressions for sure. Do not skip this section though, as we are using
Booleans to explain the technical decisions behind Wunderspec's design.

Before we start, let's import the necessary classes from our library:

<!-- name: test_booleans -->
```python
from wunderspec import *
```

## Boolean literals: True and False

Like in Python, we have two Boolean literals: `True` and `False`.

<!-- name: test_booleans -->
```python
assert repr(Val(True)) == 'Lit(True)'
assert repr(Val(False)) == 'Lit(False)'
```

In Wunderspec, we clearly distinguish between the abstract syntax tree (AST) and
the values.  This is why we call `repr` on the constructed literals. Moreover,
`repr` shows parentheses around the literals to indicate that these are AST
nodes, not raw Python values. This is important, since Wunderspec is an embedded
DSL, not a language of its own.

You can see that `Val(True)` and `Val(False)` are objects of class `BoolExpr`:

<!-- name: test_booleans -->
```python
assert Val(True).__class__.__name__ == "BoolExpr"
assert Val(False).__class__.__name__ == "BoolExpr"
```

Now, we can evaluate the literals to get their values:

<!-- name: test_booleans -->
```python
assert repr(value(Val(True))) == "True"
assert repr(value(Val(False))) == "False"
```

Notice that the evaluated values are wrapped in `V(...)` to indicate that these
are Wunderspec values, not raw Python values. Again, we find it **extremely
important to define clear boundaries between the DSL and the host language**.

## Boolean operators: And, Or, Not

As you would expect, Wunderspec supports the three basic Boolean operators:

<!-- name: test_booleans -->
```python
assert repr(And(Val(True), Val(False))) == "AND(Lit(True), Lit(False))"
assert repr(Or(Val(False), Val(True))) == "OR(Lit(False), Lit(True))"
assert repr(Not((True))) == "NOT(Lit(True))"
```

The above Python expressions create Wunderspec AST nodes representing the
Boolean operations. Our DSL supports Python coercions to omit `Lit` calls:

<!-- name: test_booleans -->
```python
assert repr(And(True, False)) == "AND(Lit(True), Lit(False))"
assert repr(Or(False, True)) == "OR(Lit(False), Lit(True))"
assert repr(Not(True)) == "NOT(Lit(True))"
```

The operators `And` and `Or` support multiple arguments:

<!-- name: test_booleans -->
```python
assert repr(And(True, False, True)) == "AND(Lit(True), Lit(False), Lit(True))"
assert repr(Or(False, False, True)) == "OR(Lit(False), Lit(False), Lit(True))"
assert repr(And()) == "AND()"
assert repr(Or()) == "OR()"
```

As is common in Python DSLs, we also override the Python operators `&`, `|`, and `~`
to represent `And`, `Or`, and `Not`, respectively:

<!-- name: test_booleans -->
```python
assert repr(Val(True) & False & True) == "AND(AND(Lit(True), Lit(False)), Lit(True))"
assert repr(Val(True) & (Val(False) | True)) == "AND(Lit(True), OR(Lit(False), Lit(True)))"
assert repr(~Val(True)) == "NOT(Lit(True))"
```

Be careful when using the operators `&`, `|`, and `~`:

 - The first argument has to be a Wunderspec Boolean expression, e.g., `Val(True)`.
   Otherwise, the overloaded operator will not be called.

 - The precedence of these operators is lower than that of comparison operators
   (e.g., `<`, `==`, etc.). Use parentheses to ensure the correct order of operations.

 - The operators `&` and `|` are always binary. Hence, chaining them will create
   nested `And` and `Or` nodes. This usually produces ugly syntax trees.

If you don't like the above limitations, you can also chain and flatten `And` and
`Or` by calling `and_` and `or_` methods:

<!-- name: test_booleans -->
```python
assert repr(Val(True).and_(False).and_(True)) == "AND(Lit(True), Lit(False), Lit(True))"
assert repr(Val(True).or_(False).or_(True)) == "OR(Lit(True), Lit(False), Lit(True))"
```

Finally, **evaluating Boolean expressions works as expected**:

<!-- name: test_booleans -->
```python
assert repr(value(And(True, False, True))) == "False"
assert repr(value(Or(False, False, True))) == "True"
assert repr(value(Not(True))) == "False"
```

It is important to note that we are **not allowed to mix Wunderspec expressions
and raw Python Boolean operators**:

<!-- name: test_booleans -->
```python
# This is NOT allowed:
try:
    Val(True) and Val(False)
except Exception as e:
    assert str(e) == "Mixing Python Booleans and Wunderspec Booleans is not allowed."
```

## Classical Implication

Similar to TLA<sup>+</sup>, Wunderspec supports implication:

<!-- name: test_booleans -->
```python
assert repr(Implies(Val(True), Val(False))) == "IMPLIES(Lit(True), Lit(False))"
assert repr(Implies(Val(False), Val(True))) == "IMPLIES(Lit(False), Lit(True))"
assert repr(Val(True).implies(False)) == "IMPLIES(Lit(True), Lit(False))"
assert repr(Val(False).implies(True)) == "IMPLIES(Lit(False), Lit(True))"
```

As expected, `x.implies(y)` is equivalent to `Not(x).or_(y)`:

<!-- name: test_booleans -->
```python
for x in [True, False]:
    for y in [True, False]:
        assert repr(value(Val(x).implies(y))) == repr(value(Not(Val(x)).or_(y)))
```

## Logical Equivalence

Boolean expressions can also be compared for equivalence, usually called
if-and-only-if. In Wunderspec, we simply use the `==` and `!=` operators for
this purpose:

<!-- name: test_booleans -->
```python
assert repr(Val(True) == False) == "EQ(Lit(True), Lit(False))"
assert repr(Val(False) == True) == "EQ(Lit(False), Lit(True))"
assert repr(Val(True) != False) == "NE(Lit(True), Lit(False))"
assert repr(Val(False) != True) == "NE(Lit(False), Lit(True))"
```

Equivalence and inequivalence is evaluated as expected:

<!-- name: test_booleans -->
```python
assert repr(value(Val(False) == False)) == "True"
assert repr(value(Val(False) == True)) == "False"
assert repr(value(Val(True) == False)) == "False"
assert repr(value(Val(True) == True)) == "True"

assert repr(value(Val(False) != False)) == "False"
assert repr(value(Val(False) != True)) == "True"
assert repr(value(Val(True) != False)) == "True"
assert repr(value(Val(True) != True)) == "False"
```

## Conditional expressions

Similar to `e1 if cond else e2` in Python, Wunderspec supports conditional
expressions. Here is how we create one for Booleans:

<!-- name: test_booleans -->
```python
cond_expr = Ite(Val(True), Val(False), Val(True))
assert repr(cond_expr) == "Ite(Lit(True), Lit(False), Lit(True))"
```

Alternatively, we can use the `if_` and `else_` methods to create conditional
expressions:

<!-- name: test_booleans -->
```python
cond_expr = Val(True).if_(Val(False)).else_(Val(False))
assert repr(cond_expr) == "Ite(Lit(False), Lit(True), Lit(False))"
```

Like the other operators, both `Ite` and `if_`/`else_` auto-coerce raw Python
literals, so the condition and branches do not all have to be wrapped in `Val`:

<!-- name: test_booleans -->
```python
cond_expr = Ite(True, False, True)
assert repr(cond_expr) == "Ite(Lit(True), Lit(False), Lit(True))"

cond_expr = Val(True).if_(False).else_(False)
assert repr(cond_expr) == "Ite(Lit(False), Lit(True), Lit(False))"
```

As we will see later, the same constructs can be used to create conditional
expressions for other types as well.