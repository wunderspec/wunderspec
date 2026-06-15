# Enumerations

Wunderspec supports Python's standard `enum.Enum` class as a sort for map
values and other symbolic expressions.  This lets you use familiar IDE
auto-completion and type safety for program-counter values, status codes,
and any other finite set of named constants.

Before we start, let's import the necessary classes from our library:

<!-- name: test_enums -->
```python
from enum import Enum, auto
from wunderspec import *
```

## Defining an enum

Define a Python `Enum` as you normally would:

<!-- name: test_enums -->
```python
class PC(Enum):
    NCS  = auto()
    TRY  = auto()
    CS   = auto()
    EXIT = auto()
```

## Wrapping enum values with `Val`

Use `Val(PC.NCS)` to lift an enum member into a Wunderspec expression:

<!-- name: test_enums -->
```python
assert repr(Val(PC.NCS)) == "Lit(PC.NCS)"
assert repr(Val(PC.CS))  == "Lit(PC.CS)"
```

## Comparing enum values

Comparison works whether the right-hand side is a raw enum member or a
`Val`-wrapped one — Wunderspec coerces automatically:

<!-- name: test_enums -->
```python
pc = Val(PC.NCS)
assert repr(value(pc == PC.NCS))       == "True"
assert repr(value(pc == Val(PC.NCS)))  == "True"
assert repr(value(pc == PC.CS))        == "False"
```

## Enums as map value sorts

Pass the enum class as the value type when creating a map — Wunderspec
infers the sort automatically:

<!-- name: test_enums -->
```python
# Map from int process-IDs to PC values
pc_map = Map(int, PC).insert(1, Val(PC.NCS)).insert(2, Val(PC.CS))
assert repr(value(pc_map[Val(1)])) == "PC.NCS"
assert repr(value(pc_map[Val(2)])) == "PC.CS"
```

Using Python type-annotation syntax inside `Annotated` fields of a `@state`
or `@record` class works identically:

```python
from typing import Annotated
from wunderspec.machine import PARAMETER, Context, MachineStateBase, state

@state
class MyState(MachineStateBase):
    N:  Annotated[Expr, int, PARAMETER]
    pc: Annotated[Expr, dict[int, PC]]   # map from int to PC
```

## Enums in sets

Enum members can appear directly as set elements:

<!-- name: test_enums -->
```python
s = Set(PC.NCS, PC.TRY, PC.CS)
assert repr(value(s.size))           == "3"
assert repr(value(s.contains(PC.CS))) == "True"
```

## Assigning enum values in actions

Inside a transition, assign `Val(PC.TRY)` to a map entry with direct
assignment on `c.state`:

```python
@action(inline=False)
def enter_try(c: Context[MyState], q):
    c.assume(c.state.pc[q] == PC.NCS)
    c.state.pc[q] = Val(PC.TRY)
```

Both `Val(PC.TRY)` and the raw enum member `PC.TRY` are accepted on the
right-hand side; Wunderspec coerces automatically.
