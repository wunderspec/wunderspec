# From TLA+ to Wunderspec

This document is a practical manual for translating typed and untyped TLA+
specifications into Wunderspec models.

It is intentionally incremental: we start with rules that are stable today and
will extend this manual as we translate more specs.

This guide uses the current shorthand annotations: `Param[T]`, `StateVar[T]`,
`Field[T]`, and `Variant[T]`. The older `Annotated[Expr, T, PARAMETER]` forms
are still accepted by Wunderspec, but new translations should prefer these
shorthands.

Worked examples to imitate: `examples/two_phase.py` (records, message set),
`examples/fpaxos.py` (set-valued constants, `@expr` helpers), and
`examples/etcdraft.py` (tagged-union messages, message *bags*, sequences/logs,
reconfiguration) — the largest end-to-end TLA+ translation in the repo.

## 1. Translation mindset

A good translation keeps the same:

- state shape
- action structure
- enabledness conditions
- invariants/properties

A good translation may still change:

- naming style (`CamelCase` in TLA+ to `snake_case` in Python)
- how nondeterminism is encoded (TLA+ logical operators vs scheduler API)
- representation of constants (CLI-friendly numeric parameters instead of set
  literals, when needed)

## 2. Minimal Wunderspec skeleton

```python
from wunderspec import Expr, Set, Val
from wunderspec.machine import (
    Context,
    MachineStateBase,
    Param,
    StateVar,
    action,
    invariant,
    state,
)


@state
class SpecState(MachineStateBase):
    N: Param[int]
    x: StateVar[int]


@action(init=True)
def init(c: Context[SpecState]):
    s = c.state
    s.x = Val(0)


@action
def step(c: Context[SpecState]):
    s = c.state
    s.x = s.x + Val(1)


@invariant
def x_nonneg(s: SpecState) -> Expr:
    return s.x >= Val(0)
```

## 3. TLA+ module elements

| TLA+ | Wunderspec |
|---|---|
| `CONSTANT C` | `C: Param[T]` in `@state` |
| `VARIABLE x` | `x: StateVar[T]` in `@state` |
| `Init == ...` | `@action(init=True) def init(c): ...` |
| `Next == ...` | `@action def step(c): ...` |
| sub-action `A(i) == ...` | `@action(inline=False) def a(c, i): ...` |
| `Inv == ...` | `@invariant def inv(s): return ...` |
| `CONSTANT` assignment (MC config) | `@instance def name() -> S: ...` |

Rule of thumb:

- Keep one Python `@action(inline=False)` per TLA+ sub-action; the top-level
  `step` wires them together (see §5.5/§5.6).
- Use `@action(init=True)` for `Init` so assignments render without primes when
  compiling to TLA+.
- Keep top-level `init` and `step` names unless there is a reason to diverge.
- `Param[T]`/`StateVar[T]`/`Field[T]`/`Variant[T]` come from `wunderspec` (or
  `wunderspec.machine`); the older `Annotated[Expr, T, PARAMETER]` spelling still
  works but is discouraged.

### 3.1 Fixing constants with `@instance`

TLA+ `CONSTANT` declarations become `PARAMETER` fields in the `@state` class.
To make a concrete parameter assignment reusable — for running, replaying, or
compiling — annotate a no-argument factory function with `@instance`:

```python
from wunderspec.machine import instance

@instance
def two_rms() -> TwoPhaseState:
    return TwoPhaseState(N=2)
```

Run it by name with `--instance`:

```bash
wunderspec run examples/two_phase.py --instance two_rms --property tc_consistent
```

`@instance` shines for set-valued constants that cannot be passed as a plain
integer on the CLI (e.g. `Quorum1=Set(Set("a1"), Set("a2"))`):

```python
@instance
def two_acceptors() -> FPaxosState:
    return FPaxosState(
        Value=Set(0, 1, 2),
        Acceptor=Set("a1", "a2"),
        Quorum1=Set(Set("a1"), Set("a2")),
        Quorum2=Set(Set("a1", "a2")),
        Ballot=Set(0, 1, 2),
    )
```

Rules of thumb:

- Define an `@instance` factory for every parameterized spec; it is the only way
  to supply a state prototype on the CLI (`--instance NAME`).
- Name each factory after the scenario it represents (`two_acceptors`,
  `n3_bounded`), not after its type.

## 4. Expression primitives

### 4.1 Literals and constructors

| TLA+ | Wunderspec |
|---|---|
| `TRUE`, `FALSE` | `Val(True)`, `Val(False)` |
| integer/string literal | `Val(42)`, `Val("x")` |
| `{}` (typed empty set) | `Set(int)`, `Set(str)`, `Set(MyTupleType)` |
| `{a, b, c}` | `Set(a, b, c)` |
| `<<a, b>>` | `Tuple(a, b)` |
| `i..j` | `Set(i, ..., j)` or `Interval(i, j)` |

Notes:

- Wunderspec expressions are not raw Python values; wrap concrete values with
  `Val(...)` unless already in expression form.
- Avoid Python `and/or/not`; use Wunderspec operators (`And`, `Or`, `~`, etc.).

### 4.2 Boolean and arithmetic operators

| TLA+ | Wunderspec |
|---|---|
| `/\`, `\/` | `And(...)`, `Or(...)` |
| `~` | `~expr` or `Not(expr)` |
| `=>` | `lhs.implies(rhs)` or `Implies(lhs, rhs)` |
| `=`, `#` | `==`, `!=` |
| `< <= > >=` | same operators |
| `+ - * \div %` | `+ - * / %` (`/` is integer division) |

### 4.3 Sets

| TLA+ | Wunderspec |
|---|---|
| `x \in S` | `S.contains(x)` or `x.in_(S)` |
| `x \notin S` | `~S.contains(x)` |
| `S \cup T` | `S | T` |
| `S \cap T` | `S & T` |
| `S \ T` | `S - T` |
| `S \subseteq T` | `S <= T` |
| `\A x \in S: P` | `S.forall(lambda x: P)` |
| `\E x \in S: P` | use `with c.one_of(S, "x") as x: c.assume(P)` inside actions, or `S.exists(lambda x: P)` in expressions |

### 4.4 Functions/maps

| TLA+ | Wunderspec |
|---|---|
| `[x \in S |-> e(x)]` | `S.map_to(lambda x: e(x))` |
| `f[x]` | `f[x]` |
| `[f EXCEPT ![k] = v]` (assigning state) | `s.f[k] = v` (see §5.4) |
| `[f EXCEPT ![k] = v]` (as a value) | `f.replace(k, v)` (key must exist) |
| `[f EXCEPT ![k] = v]` inserting a new key | `f.insert(k, v)` |
| `DOMAIN f` | `f.keys` |
| `{f[x] : x \in DOMAIN f}` | `f.values` |
| fold over `DOMAIN f` (e.g. via `FoldFunction`) | `f.reduce(lambda acc, k, v: e, init)` |

`f.replace(k, v)` is *replace-only* (the key is expected to exist); use
`f.insert(k, v)` for insert-or-update. When you assign a keyed update directly to
a state field whose key may be new, declare the field
`StateVar[dict[K, V], UPSERT]` (`UPSERT` from `wunderspec.machine`).
Prefer the direct-assignment form `s.f[k] = v` for state (see §5.4); reserve
`.replace`/`.insert` for building a value that feeds a larger expression.

### 4.5 Sequences/lists

| TLA+ | Wunderspec |
|---|---|
| `<< >>` (empty sequence) | `List(T)` |
| `Append(seq, x)` | `seq + List(x)` (there is no `.append`) |
| `Head(seq)` | `seq[0]` |
| `Tail(seq)` | `seq[1:]` |
| `Len(seq)` | `seq.size` |
| `seq[i]` (1-based) | `seq[i - 1]` |
| `SubSeq(seq, m, n)` (1-based, inclusive) | `seq[m - 1 : n]` |
| `DOMAIN seq` (`1..Len`) | `Interval(Val(1), seq.size)` |

**Indexing base differs.** TLA+ sequences are **1-based**; Wunderspec lists are
**0-based** (Python). Translate every positional access: `log[k]` → `log[k - 1]`,
`SubSeq(log, 1, c)` → `log[0:c]`. Keep protocol indices (commit index, match
index, `mprevLogIndex`, ...) as the 1-based integers they are in the source, and
shift only at the point of list access. A small helper keeps this honest:

```python
def entry_at(log, n):       # TLA+ log[n], 1-based
    return log[n - 1]
```

`examples/etcdraft.py` carries the per-server Raft logs this way throughout.

### 4.6 Records and message schemas

When a TLA+ model uses record-shaped messages, prefer Wunderspec `@record`
types over positional tuples. This makes field usage explicit and avoids index
mixups in large protocols.

| TLA+ | Wunderspec |
|---|---|
| `[bal |-> b]` | `Msg1A(bal=b)` |
| `[acc |-> a, bal |-> b, mbal |-> mb, mval |-> mv]` | `Msg1B(acc=a, bal=b, mbal=mb, mval=mv)` |
| `m.bal`, `m.acc` | `m.bal`, `m.acc` |
| `SUBSET [bal : Ballot]` | `set[Msg1A]` in state typing |

Example:

```python
from wunderspec import Field, record

@record
class Msg1B:
    acc: Field[str]
    bal: Field[int]
    mbal: Field[int]
    mval: Field[int]

msg = Msg1B(acc=a, bal=b, mbal=mb, mval=mv)
msg.bal                       # field access
msg2 = msg.replace(bal=b2)    # a new record value (pure update)
```

Rule of thumb:

- Use `@record` for protocol messages that are conceptually records in TLA+.
- Use tuples mainly for lightweight positional data where field names add no value.

**Fields shadow methods.** A field named like an `Expr` member (`node`, `sort`,
`replace`, `keys`, ...) shadows that member on the record. Reach the hidden
member through the `._` escape hatch:

```python
@record
class Entry:
    term: Field[int]
    value: Field[int]
    keys: Field[set[int]]   # shadows Expr/Map .keys

e.keys        # the *field* (a set)
e._.replace(term=Val(5))  # the record method, not a field
```

### 4.7 Tagged unions (`@union`)

A TLA+ value that ranges over several shapes — distinguished by a tag field —
maps to a Wunderspec `@union`. Variants carry a typed payload (a record, tuple,
or scalar) or `Unit` for no payload:

```python
from wunderspec import Unit, Variant, union

@union
class Option:
    Some: Variant[int]
    None_: Variant[Unit]

Option.Some(Val(3))   # construct a variant
Option.None_()        # no-payload variant
opt.tag               # the variant name, a StrExpr ("Some" / "None_")
opt.match(Some=lambda v: v, None_=lambda: Val(-1))   # pattern match (a value)
```

For **heterogeneous messages** (e.g. Raft's four RPC types), prefer an
*envelope record* of the common fields plus a `payload` union of the
per-type bodies. This keeps common-field access flat while staying type-safe:

```python
@record
class RVReq:
    mlastLogTerm: Field[int]
    mlastLogIndex: Field[int]

@record
class AEReq:
    mprevLogIndex: Field[int]
    mentries: Field[list[LogEntry]]
    # ...

@union
class Payload:
    RequestVoteReq: Variant[RVReq]
    AppendEntriesReq: Variant[AEReq]
    # ...

@record
class Message:
    mterm: Field[int]
    msource: Field[int]
    mdest: Field[int]
    payload: Field[Payload]
```

A union is a single sort, so a `Message` is usable as a set/bag element and a map
key. See §5.7 for dispatching on the tag inside an action, and
`examples/etcdraft.py` for the full pattern.

### 4.8 Conditionals (`IF`/`THEN`/`ELSE`)

| TLA+ | Wunderspec |
|---|---|
| `IF p THEN a ELSE b` | `Ite(p, a, b)` or `a.if_(p).else_(b)` |

`Ite` auto-coerces raw literal branches; both branches must have the same sort.
Use a conditional *value* to fold a one-variable `EXCEPT`-with-`IF` into a single
assignment instead of splitting the action:

```python
s.votes_granted[i] = Ite(granted, s.votes_granted[i] | Set(j), s.votes_granted[i])
```

### 4.9 Powersets, function sets, and quorums

| TLA+ | Wunderspec |
|---|---|
| `SUBSET S` | `AllSubsets(S)` |
| `[S -> T]` (function set) | `AllMaps(S, T)` |
| `S \X T` (Cartesian product) | `AllTuples(S, T)` |
| `[f1: S1, f2: S2]` (record set) | `AllRecords(...)` |

A quorum predicate `S \in Quorum(c)` (majority subset) is most cheaply written
as a membership test, and the set of all quorums via a filtered powerset:

```python
def is_quorum(subset, conf):           # subset \in Quorum(conf)
    return And(subset.issubset(conf), subset.size * 2 > conf.size)

def quorums(conf):                     # Quorum(conf)
    return AllSubsets(conf).filter(lambda q: q.size * 2 > conf.size)
```

### 4.10 Message sets and bags (TLA+ `Bags`)

A simple network is a **set** of in-flight messages: send with union, receive by
choosing a member and asserting membership.

```python
s.msgs = s.msgs | Set(msg)                       # send
with c.one_of(s.msgs, "m") as m:                 # receive
    c.assume(s.msgs.contains(m))
```

When the source uses the `Bags` module (a *multiset* — messages can be
duplicated, dropped one copy at a time, counted), model it with the user-space
`Bag` ADT from `examples/bags.py` (no core changes needed). A bag is a
`dict[Element, int]` of positive counts; bridge with `Bag(state_var)` on read and
`bag.as_map` on write:

| TLA+ (`Bags`) | Wunderspec (`Bag`) |
|---|---|
| `EmptyBag` | `Bag.empty(Element).as_map` |
| `WithMessage(m, B)` / `B (+) SetToBag({m})` | `Bag(B).add_one(m)` |
| `WithoutMessage(m, B)` | `Bag(B).remove_one(m)` |
| `BagToSet(B)` / `DOMAIN B` | `Bag(B).to_set()` |
| `m \in DOMAIN B` | `Bag(B).contains(m)` |
| `B[m]` (CopiesIn) | `Bag(B)[m]` |
| `B1 (+) B2` / `B1 (-) B2` | `Bag(B1) + Bag(B2)` / `Bag(B1) - Bag(B2)` |

```python
s.messages = Bag(s.messages).remove_one(m).as_map     # Discard(m)
```

`examples/etcdraft.py` carries `messages` and `pendingMessages` as bags, with a
`Ready(i)` action flushing one server's pending bag into the network — and
duplicate/drop fault actions that rely on the multiplicity count.

### 4.11 `Min`/`Max` of a set

There is no built-in `Min`/`Max`; fold with `reduce`. For a set of non-negative
integers, `MaxOrZero(S)` (0 when empty) is:

```python
def max_or_zero(s_set):
    return s_set.reduce(lambda acc, x: Ite(x > acc, x, acc), 0)
```

`Min({a, b})` / `Max({a, b})` over two values is just `Ite(a < b, a, b)` /
`Ite(a > b, a, b)`.

## 5. Action translation rules

TLA+ actions describe a relation between current (`x`) and next (`x'`) state.
In Wunderspec, write imperative-style updates on `c.state` plus guards.

### 5.1 Guards

TLA+:

```tla
/\ x > 0
/\ y \in S
```

Wunderspec:

```python
c.assume(s.x > Val(0))
c.assume(s.S.contains(s.y))
```

### 5.2 Primed assignments

TLA+:

```tla
/\ x' = e
/\ y' = f
```

Wunderspec:

```python
s.x = e
s.y = f
```

### 5.3 `UNCHANGED`

No explicit code is needed for unchanged variables. Variables you do not assign
remain unchanged.

### 5.4 Nested updates (`EXCEPT` on maps/records)

**Prefer direct assignment on `s`.** Map entries, nested paths, and record
fields all assign directly — this is the idiomatic translation of `EXCEPT`:

```python
s.rm_state[rm] = Val("prepared")              # [rmState EXCEPT ![rm] = "prepared"]
s.match_index[i][j] = m.mmatch_index          # [matchIndex EXCEPT ![i][j] = ...]
s.config[i] = Config(incoming=new, ...)       # whole-field replacement
```

For a top-level replacement, assign the whole field:

```python
s.msgs = s.msgs | Set(mk_prepared(rm))
```

Reserve `f.replace(k, v)` / `rec.replace(**fields)` / `.edit()` for building a
**new value that is not bound to a state path** — a record to put in a message, a
helper's return value, or a branch of an `Ite(...)`. When the target *is* a state
path, write `s.f[k] = v`, not `s.f = s.f.replace(k, v)`.

Direct assignment already covers multi-field updates (each step is atomic).
Assignment is immediate, so to read the *unprimed* (pre-update) value of a field
you also write, snapshot it in a local first:

```python
old_x, old_y = s.x, s.y     # capture unprimed values
s.x = old_y                 # x' = y
s.y = old_x                 # y' = x
```

`with s.editing() as upd:` does this snapshotting automatically across a block,
for when several interdependent fields make manual locals awkward.

### 5.5 Disjunction (`\/`) in actions

Use scheduler alternatives:

```python
alts = iter(c.alternatives("A", "B", "C"))
with next(alts):
    action_a(c)
with next(alts):
    action_b(c)
with next(alts):
    action_c(c)
```

### 5.6 Existential choice in `Next`

TLA+:

```tla
\E rm \in RM : RMAction(rm)
```

Wunderspec:

```python
with c.one_of(rms(s), "rm") as rm:
    rm_action(c, rm)
```

### 5.7 Dispatching on a union tag inside an action

`.match` is an *expression* (it returns one value), so to drive **different state
updates per variant** dispatch on the tag with `c.assume`, then unwrap the
payload once with `match(..., default=...)`. The `default` is unreachable after
the tag guard but is needed so all variants share a sort:

```python
def receive(c, m):                       # ReceiveDirect(m)
    s = c.state
    alts = iter(c.alternatives("UpdateTerm", "RequestVoteReq", "AppendEntriesReq"))
    with next(alts):
        update_term(c, m)                # reads only common fields: m.mterm, ...
    with next(alts):
        c.assume(m.payload.tag == "RequestVoteReq")
        p = m.payload.match(RequestVoteReq=lambda x: x, default=_DUMMY_RVREQ)
        handle_request_vote(c, m, p)     # p.mlastLogTerm, ...
    with next(alts):
        c.assume(m.payload.tag == "AppendEntriesReq")
        p = m.payload.match(AppendEntriesReq=lambda x: x, default=_DUMMY_AEREQ)
        handle_append_entries(c, m, p)
```

If a union obtained as a bare `Expr` lacks `.match`, wrap it:
`UnionExpr(u.node).match(...)`.

### 5.8 `CHOOSE` and `LET`/`IN`

`CHOOSE x \in S : P(x)` is `S.choose(lambda x: P(x))` (a deterministic pick).

`LET a == e1 IN ...` becomes an ordinary Python local: `a = e1`. If the bound
expression is large and reused, and you want it to appear *once* in the emitted
spec, bind it with `c.cache`:

```python
quorum = c.cache(votes & members, "quorum")
```

## 6. Temporal layer

TLA+ top-level:

```tla
Spec == Init /\ [][Next]_vars
```

In Wunderspec this is implicit in `init` + `step` and how `wunderspec run` or
`wunderspec replay` executes traces.

Fairness operators are available (`WeakFair`, `StrongFair`) as formula
constructors, typically in helper functions.

## 7. Invariants and properties

Keep invariants as pure functions over state:

```python
def tc_consistent(s: TwoPhaseState):
    return rms(s).forall(
        lambda rm1: rms(s).forall(
            lambda rm2: ~And(
                s.rm_state[rm1] == Val("aborted"),
                s.rm_state[rm2] == Val("committed"),
            )
        )
    )
```

Add a `@coverage` predicate to steer random simulation toward interesting states
(typically a tuple of the mutable variables):

```python
@coverage
def state_cov(s: TwoPhaseState) -> Expr:
    return Tuple(s.tm_state, s.rm_state, s.msgs)
```

Useful CLI checks (a parameterized spec runs through its `@instance` factory):

```bash
# random simulation; ALWAYS pass --timeout so CI cannot run unbounded
wunderspec run examples/two_phase.py --instance two_rms \
    --property tc_consistent --max-samples 100 --max-steps 20 --timeout 30

# bounded exhaustive model check
wunderspec check examples/two_phase.py --instance two_rms \
    --property tc_consistent --max-steps 6 --timeout 30
```

`--property` infers the kind from the decorator (`@invariant`/`@example`/
`@temporal`). Register the spec in `examples/examples.yaml` (with a `timeout:`)
to have the example harness exercise it.

## 8. Naming and style conventions

- TLA+ `Init`/`Next` -> Python `init`/`step`.
- Convert `CamelCase` identifiers to `snake_case` for Python readability.
- Keep comments that explain protocol intent, not syntactic trivia.
- Use explicit imports (`from wunderspec import A, B, C`) in examples to keep
  flake8 happy.

## 9. Common pitfalls

- Do not use Python `and/or/not` with expressions; use Wunderspec boolean ops.
- Assign nested state fields directly (`s.rm_state[rm] = ...`); reserve
  `.replace`/`.edit` for pure values. Use `with s.editing()` only when updates
  must read the pre-update state atomically.
- `c.assume(...)` is mandatory for action guards; forgetting it over-approximates
  behavior.
- TLA+ sequences are 1-based, Wunderspec lists are 0-based — shift at every list
  access (`log[k]` → `log[k - 1]`); keep protocol indices 1-based.
- **Bound unbounded specs for model checking.** Add `Param` caps (e.g.
  `MaxTerm`, `MaxLogLen`) and guard the space-growing actions with `c.assume`
  (`c.assume(s.current_term[i] < s.MaxTerm)`). Document these as the only
  deviations from the source.
- If set-valued parameters are awkward to pass via CLI, use numeric parameters
  (`NumX`) and derive sets (`1..NumX`) in the model.
- Keep alternative labels stable (`"TMCommit"`, `"TMAbort"`, ...). They are part
  of replay schedules.

## 10. Two-phase example mapping

TLA+ (fragment):

```tla
TMCommit ==
  /\ tmState = "init"
  /\ tmPrepared = RM
  /\ tmState' = "committed"
  /\ msgs' = msgs \union { MkCommit }
  /\ UNCHANGED <<rmState, tmPrepared>>
```

Wunderspec:

```python
@action(inline=False)
def tm_commit(c: Context[TwoPhaseState]):
    s = c.state
    c.assume(s.tm_state == Val("init"))
    c.assume(s.tm_prepared == rms(s))
    s.tm_state = Val("committed")
    s.msgs = s.msgs | Set(mk_commit())
```

## 11. Further reading and open TODOs

Covered above: records & tagged unions (§4.6–4.7), `LET`/`CHOOSE` (§5.8),
conditionals (§4.8), powersets/function-sets/quorums (§4.9), message sets &
`Bags` (§4.10), sequences & 1-based indexing (§4.5), and bounding for model
checking (§9). Larger worked translations:

- `examples/etcdraft.py` — etcd-raft: union messages, message bags,
  per-server logs, reconfiguration, the four message handlers.
- `examples/two_phase.py` — records and a message set.
- `examples/fpaxos.py` — set-valued constants and `@expr` helpers.

Still open:

- Liveness/fairness: `WeakFair`/`StrongFair` formula constructors with
  `@temporal` properties, checked via `wunderspec with-tlc`/`with-apalache`.
- Module instantiation / `INSTANCE` and parameter substitution.
- Translating PlusCal-generated TLA+.
