# From Quint to Wunderspec

This document is a practical manual for translating Quint specifications into
Wunderspec models, based on the translation of the Minimmit BFT consensus
protocol (`replica.qnt` → `examples/minimmit.py`).

It is intentionally incremental: we start with rules that are stable today and
will extend this manual as we translate more specs.

This guide uses the current shorthand annotations: `Param[T]`,
`StateVar[T]`, `Field[T]`, and `Variant[T]`. The older `Annotated[...]`
forms are still accepted by Wunderspec, but new translations should prefer
these shorthands.

## 1. Translation mindset

A good translation keeps the same:

- state shape
- action structure
- enabledness conditions (guards)
- invariants and properties

A good translation may still change:

- naming style (`camelCase`/`SCREAMING_SNAKE` in Quint → `snake_case` in Python)
- how nondeterminism is encoded (Quint `oneOf`/`nondet` → scheduler API)
- how tagged unions (`Option`, `Result`, ...) are represented (use `@union`)
- embedded module imports (inline definitions from imported `.qnt` files)

## 2. Minimal Wunderspec skeleton

```python
from wunderspec import *


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
```

## 3. Quint module elements

| Quint | Wunderspec |
|---|---|
| `const C: T` | `C: Param[T]` in `@state` |
| `var x: T` | `x: StateVar[T]` in `@state` |
| `action init = { ... }` | `@action(init=True) def init(c): ...` |
| `action step = { ... }` | `@action def step(c): ...` |
| `action foo = { ... }` (sub-action) | `@action(inline=False) def foo(c, ...): ...` |
| `pure val f = ...` / `pure def f(...) = ...` | `@expr def f(...) -> T: ...` when the helper returns a symbolic expression |
| `val f = ...` / `def f(...) = ...` | `@expr def f(...) -> T: ...` for expression helpers; plain Python functions for non-symbolic scaffolding |
| `val inv: bool = ...` | `@invariant def inv(s): return ...` |
| `type T = { field: U, ... }` | `@record class T: field: Field[U]` |
| `type T = A(U) \| B` (tagged union) | `@union class T: A: Variant[U]; B: Variant[Unit]` |

Rule of thumb:

- Keep one `@action(inline=False)` per Quint `action` definition.
- Keep top-level `init` and `step` names unless there is a reason to diverge.
- Pure helpers (`pure def`, `pure val`) become `@expr` functions when they
  should be represented in the symbolic expression tree. They accept
  `s: MyState` as their **first** argument if they reference state.
- Quint type aliases (`type ViewNumber = int`) become Python type annotations
  or comments — no runtime objects needed.

### 3.1 Handling imported Quint modules

Quint specs often `import` definitions from other `.qnt` files. The recommended
approach is to **inline** all imported definitions at the top of the Python
file, grouped by source module, and document their origin:

```python
# ── Embedded from types.qnt ──────────────────────────────────────────────────

GENESIS_BLOCK: str = "GENESIS_BLOCK"
GENESIS_VIEW: int = -1
NOTARIZE_KIND: str = "NOTARIZE_KIND"

@record
class Vote:
    view: Field[int]
    block: Field[str]
    sig: Field[str]
    kind: Field[str]
```

This avoids creating additional Python modules for definitions that are
conceptually part of a single flat specification.

### 3.2 Fixing constants with `@instance`

Quint `const` declarations become `Param[...]` fields in the `@state` class.
To make a concrete parameter assignment reusable, annotate a no-argument
factory function with `@instance`:

```python
@instance
def n5_f1() -> MinimmitState:
    return MinimmitState(N=5, F=1, ...)
```

Rules of thumb:

- Define an `@instance` factory for every parameterized spec; `--instance NAME`
  is the only way to supply a state prototype on the CLI.
- Name each factory after the scenario it represents (`n5_f1`, `two_actors`).

## 4. Expression primitives

### 4.1 Literals and constructors

| Quint | Wunderspec |
|---|---|
| `true`, `false` | `Val(True)`, `Val(False)` |
| integer/string literal | `Val(42)`, `Val("x")` |
| `Set()` (typed empty set) | `Set(int)`, `Set(str)`, `Set(MyRecord)` |
| `Set(a, b, c)` | `Set(a, b, c)` |
| `(a, b)` (tuple) | `Tuple(a, b)` |
| `1.to(n)` | `Set(Val(1), ..., n)` or `Interval(1, n)` |
| `[k -> v]` (map literal) | `Map((k, v))` |
| `List()` (empty sequence) | `List(T)` |

### 4.2 Boolean and arithmetic operators

| Quint | Wunderspec |
|---|---|
| `and { ... }` | `And(...)` |
| `or { ... }` | `Or(...)` |
| `not(e)` | `~e` or `Not(e)` |
| `e1 implies e2` | `e1.implies(e2)` or `Implies(e1, e2)` |
| `==`, `!=` | `==`, `!=` |
| `<`, `<=`, `>`, `>=` | same operators |
| `+`, `-`, `*`, `/`, `%` | same operators (`/` is integer division) |

### 4.3 Sets

| Quint | Wunderspec |
|---|---|
| `S.contains(x)` | `S.contains(x)` |
| `S.union(T)` | `S \| T` |
| `S.intersect(T)` | `S & T` |
| `S.exclude(T)` | `S - T` |
| `S.subseteq(T)` | `S <= T` |
| `S.forall(x => P)` | `S.forall(lambda x: P)` |
| `S.exists(x => P)` | `S.exists(lambda x: P)` (in expressions) |
| `S.filter(x => P)` | `S.filter(lambda x: P)` |
| `S.map(x => e)` | `S.map(lambda x: e)` |
| `S.size()` | `S.size` |
| `S.powerset()` | `AllSubsets(S)` |
| `Maps(S, T)` | `AllMaps(S, T)` |
| `tuples(S, T)` | `AllTuples(S, T)` |
| record set `[f1: S1, ...]` | `AllRecords(...)` |
| `S.fold(init, (acc, x) => f)` | `S.reduce(lambda acc, x: f, init)` |

A deterministic pick (CHOOSE-like) in an *expression* is `S.choose(lambda x: P)`
(inside an action, use `c.one_of(S, "x")` for nondeterministic choice instead).
A majority/quorum predicate is a subset membership test:

```python
def is_quorum(subset, conf):
    return And(subset.issubset(conf), subset.size * 2 > conf.size)
```

### 4.4 Maps (Quint records with `->`)

| Quint | Wunderspec |
|---|---|
| `m.get(k)` / `m[k]` | `m[k]` |
| `m.set(k, v)` (key exists) | `m.replace(k, v)` (value) / `s.m[k] = v` (state) |
| `m.put(k, v)` (insert-or-update) | `m.insert(k, v)` |
| `m.setBy(k, f)` | `m.replace(k, f(m[k]))` |
| `S.mapBy(x => e)` | `S.map_to(lambda x: e)` |
| `m.keys()` | `m.keys` |
| `m.values()` | `m.values` |
| `m.keys().fold(i, (acc, k) => f(acc, k, m.get(k)))` | `m.reduce(lambda acc, k, v: f(acc, k, v), i)` |

`m.replace(k, v)` is *replace-only* (the key must already exist); `m.insert(k, v)`
inserts or updates. For a state map whose keyed assignment may add a new key,
declare it `StateVar[dict[K, V], UPSERT]` (`UPSERT` from `wunderspec.machine`).

### 4.5 Sequences/lists

| Quint | Wunderspec |
|---|---|
| `List()` (empty) | `List(T)` |
| `l.append(x)` | `l + List(x)` |
| `l.head()` | `l[0]` |
| `l.tail()` | `l[1:]` |
| `l.length()` | `l.size` |
| `l.slice(i, j)` | `l[i:j]` |
| `l.filter(x => P)` | `l.filter(lambda x: P)` |

### 4.6 Records and `@record` types

Quint `type` definitions with named fields map directly to Wunderspec `@record`
classes. Field access uses the same dot notation.

Quint:
```quint
type Vote = {
  view: ViewNumber,
  block: Block,
  sig: Signature,
  kind: Kind,
}
```

Wunderspec:
```python
@record
class Vote:
    view: Field[int]
    block: Field[str]
    sig: Field[str]
    kind: Field[str]

v = Vote(view=view, block=block, sig=sig, kind=kind)
v.view   # field access
```

### 4.7 Record updates (Quint spread syntax)

When the spread produces a **value** (a record to send, return, or feed into a
larger expression), translate `{ ...rec, field: val }` with `.replace()`:

Quint:
```quint
{ ...self, nullified: true, timer_cancelled: true }
```

Wunderspec:
```python
self_rec.replace(nullified=Val(True), timer_cancelled=Val(True))
```

When the spread **writes back to a state variable**, prefer direct assignment
(see §5.4) over a `.replace` chain:

Quint:
```quint
replica_state' = replica_state.set(id, { ...self, view: new_view })
```

Wunderspec:
```python
s.replica_state[id].view = new_view        # not s.replica_state.replace(id, ...)
```

For multiple updates or deeply nested updates of a plain (non-state)
expression, use `.edit()` / `.result`:

```python
upd = self_rec.edit()
upd.nullified = Val(True)
upd.timer_cancelled = Val(True)
self_rec2 = upd.result
```

Inside actions, write nested updates back to state fields with direct
assignment on `s`:

```python
s.replica_state[id].view = new_view
s.replica_state[id].notarized[new_view] = block
```

Direct assignment is immediate, so updating several independent fields needs
nothing special. When a right-hand side must read the **pre-update** value of a
field another statement in the same step writes (a swap, or `x' = y /\ y' = x`),
snapshot it in a local first:

```python
old_x, old_y = s.x, s.y     # capture pre-update values
s.x = old_y
s.y = old_x
```

`s.editing()` does the same snapshotting automatically across a block, for when
several interdependent fields make manual locals awkward:

```python
with s.editing() as upd:
    upd.x = s.y      # reads the old y
    upd.y = s.x      # reads the old x (pre-update)
```

By default, a keyed assignment to a state map field is `replace_only`: the key
is expected to exist already. When translating a Quint update that may insert a
new map entry, declare the field with the `UPSERT` marker:
`StateVar[dict[K, V], UPSERT]`.

### 4.8 Conditional expressions (`if/else`)

Quint `if`/`else` in pure expressions becomes `Ite(cond, then_expr, else_expr)`:

Quint:
```quint
if (view < new_view) new_view else view
```

Wunderspec:
```python
Ite(self_rec.view < new_view, new_view, self_rec.view)
```

Use `Ite` for conditional assignments inside actions when both branches modify
the same variable but you do not want to split the action:

```python
s.store_certificate = Ite(
    is_new_cert,
    s.store_certificate.replace(id, s.store_certificate[id] | Set(cert)),
    s.store_certificate,
)
```

### 4.9 Quantifiers in invariants

For invariants that universally quantify over multiple sets (e.g., all pairs of
replicas and views), use the `Forall` generator form:

```python
return Forall(check(id, v) for id in s.CORRECT for v in s.VIEWS)
```

This is the direct analogue of Quint's nested `forall`:

```quint
CORRECT.forall(id => VIEWS.forall(v => check(id, v)))
```

### 4.10 `let ... in` and shared subexpressions

Quint `val a = e ...` / `let a = e { ... }` becomes an ordinary Python local:
`a = e`. If `e` is large and reused and you want it emitted **once** in the
compiled spec, bind it with `c.cache`:

```python
quorum = c.cache(votes & members, "quorum")
```

### 4.11 Multisets / bags

Quint state that is conceptually a *multiset* (counts matter — messages can be
duplicated or removed one copy at a time) maps to the user-space `Bag` ADT from
`examples/bags.py` (a `dict[Element, int]` of positive counts):

```python
from bags import Bag

s.messages = Bag(s.messages).add_one(msg).as_map      # add a copy
s.messages = Bag(s.messages).remove_one(msg).as_map   # remove one copy
Bag(s.messages).to_set()                              # the set of distinct elements
Bag(s.messages)[msg]                                  # how many copies
```

For heterogeneous messages, wrap an envelope record around a `@union` payload
(see §5.7 and `examples/etcdraft.py`).

## 5. Action translation rules

Quint actions describe guarded assignments. In Wunderspec, write guards with
`c.assume(...)` and assignments on `c.state`.

### 5.1 Guards (`all { guard, ... }`)

Quint:
```quint
action proposer_step(id, new_block) = all {
  id == leader.get(self.view),
  not(self.propose_sent),
  ...
}
```

Wunderspec:
```python
@action(inline=False)
def proposer_step(c: Context[MinimmitState], id: Expr, new_block: Expr):
    s = c.state
    self_rec = s.replica_state[id]
    c.assume(id == s.leader[self_rec.view])
    c.assume(~self_rec.propose_sent)
    ...
```

### 5.2 Primed assignments (`x' = e`)

Quint uses `x' = e` for state updates inside actions. In Wunderspec, assign
directly on `s`:

Quint:
```quint
sent_vote' = sent_vote.union(Set(notarize_vote))
```

Wunderspec:
```python
s.sent_vote = s.sent_vote | Set(notarize_vote)
```

### 5.3 `UNCHANGED` (implicit)

Variables you do not assign remain unchanged. No explicit code is needed.

### 5.4 Nested map/record updates

**Prefer direct assignment on `s`** for state updates — map entries, nested
paths, and record fields all assign directly. This is the idiomatic translation
of a Quint `set`/spread that writes back to a state variable:

Quint:
```quint
replica_state' = replica_state.set(id, { ...self, propose_sent: true })
```

Wunderspec:
```python
s.replica_state[id].propose_sent = Val(True)
```

Several independent updates are just several assignments:

```python
s.replica_state[id].propose_sent = Val(True)
s.replica_state[id].notarized[proposal.view] = proposal.block
```

Nested maps (map-of-maps, map-of-sets) work the same way:

Quint:
```quint
store_vote' = store_vote.set(id, store_vote.get(id).union(votes))
```

Wunderspec:
```python
s.store_vote[id] = s.store_vote[id] | votes
```

`.replace(...)`/`.edit()` are for building a **value not bound to a state path** —
a record to put in a message, a helper's return value, or a branch of an
`Ite(...)`. When the target *is* a state path, write `s.x[k] = v`, not
`s.x = s.x.replace(k, v)`.

### 5.5 Disjunction (`any { ... }`) in actions

Quint's `any { A, B, C }` becomes `c.alternatives()`:

Quint:
```quint
action step = any {
  tm_commit(c),
  tm_abort(c),
  rm_action(c),
}
```

Wunderspec:
```python
@action
def step(c: Context[MyState]):
    alts = iter(c.alternatives("TMCommit", "TMAbort", "RMAction"))
    with next(alts):
        tm_commit(c)
    with next(alts):
        tm_abort(c)
    with next(alts):
        rm_action(c)
```

Alternative labels are stable strings; they appear in replay schedules.

### 5.6 Existential choice (`nondet x = S.oneOf()`)

Quint:
```quint
nondet id = CORRECT.oneOf()
correct_replica_step(id)
```

Wunderspec:
```python
with c.one_of(s.CORRECT, "id") as id:
    correct_replica_step(c, id)
```

Multiple simultaneous choices can be combined:

```python
with (
    c.one_of(s.VIEWS, "view") as view,
    c.one_of(all_blocks(s), "block") as block,
    c.one_of(s.BYZANTINE, "sig") as sig,
):
    ...
```

**Nondeterministic subset choice** (`S.powerset().oneOf()`) — use
`c.one_of(AllSubsets(S), "name")`:

Quint:
```quint
nondet senders = BYZANTINE.powerset().oneOf()
```

Wunderspec:
```python
with c.one_of(AllSubsets(s.BYZANTINE), "senders") as senders:
    ...
```

This is commonly used in adversary models to let the scheduler choose an
arbitrary subset, including the empty set. A frequent pattern in Quint is to
map the chosen subset to a set of *identical* values (making the subset choice
equivalent to "inject 0 or 1 copies"):

Quint:
```quint
nondet senders = BYZANTINE.powerset().oneOf()
nondet sig     = BYZANTINE.oneOf()          // sig does NOT depend on senders
val votes = senders.map(s => { view: view, block: block, sig: sig, kind: k })
sent_vote' = sent_vote.union(votes)
```

Because every element of `votes` is identical (the lambda ignores its
argument), the set collapses to `{}` (senders empty) or `{vote}` (senders
non-empty). Translate faithfully — do not shortcut to always injecting one
element:

```python
with (
    c.one_of(AllSubsets(s.BYZANTINE), "byz_senders") as byz_senders,
    c.one_of(s.BYZANTINE, "byz_sig") as byz_sig,
    ...
):
    byz_vote = Vote(view=byz_view, block=byz_block, sig=byz_sig, kind=byz_k)
    s.sent_vote = s.sent_vote | byz_senders.map(lambda _s: byz_vote)
```

### 5.7 The `Option` type — translate with `@union`

Quint's `Option[T]` (variants `Some(payload)` and `None`) maps directly to a
Wunderspec `@union` type. Define it once near the top of the file, after the
payload type:

Quint:
```quint
type Option[T] = Some(T) | None
```

Wunderspec:
```python
@union
class OptionCertificate:
    Some: Variant[Certificate]
    None_: Variant[Unit]   # Unit means no payload
```

**Constructor calls** — `Some` takes a payload, `None_` takes no arguments:

```python
# returns OptionCertificate (a UnionExpr)
OptionCertificate.Some(cert)
OptionCertificate.None_()
```

**Building an option value in a pure function** — use `Ite` (both branches
have the same `UnionSort`, so the types match):

Quint:
```quint
def create_notarization(id, view, block, votes): Option[Certificate] = {
    val similar_votes = votes.filter(...)
    val votes_count = similar_votes.size()
    if (votes_count < M)
        None
    else
        Some({ view: view, block: block, signatures: ..., kind: ... })
}
```

Wunderspec:
```python
def create_notarization(
    s: MinimmitState, id: Expr, view: Expr, block: Expr, votes: Expr
) -> OptionCertificate:
    similar_votes = votes.filter(
        lambda v: (v.view == view) & (v.kind == Val(NOTARIZE_KIND)) & (v.block == block)
    )
    votes_count = similar_votes.size
    cert_kind = Ite(votes_count >= L_quorum(s), Val(FINALIZATION_KIND), Val(NOTARIZATION_KIND))
    cert = mk_certificate(view, block, similar_votes.map(lambda v: v.sig), id, cert_kind)
    return Ite(                          # type: ignore[return-value]
        votes_count >= M_quorum(s),
        OptionCertificate.Some(cert),
        OptionCertificate.None_(),
    )
```

**Pattern matching in a pure expression** — use `.match()`:

Quint:
```quint
match cert_opt {
  | Some(cert) => cert.view
  | None       => -1
}
```

Wunderspec:
```python
cert_opt.match(
    Some=lambda cert: cert.view,
    None_=lambda: Val(-1),
)
```

**Action branching on an option** — use `c.split()` on `.tag`, then
`.match()` to extract the payload. Both branches must be entered:

Quint:
```quint
match create_notarization(id, view, block, new_store) {
  | Some(cert) => _process_certificate(id, cert, is_new_cert)
  | None       => {}
}
```

Wunderspec:
```python
cert_opt = create_notarization(s, id, view, block, new_store)
(cert_br, no_cert_br) = c.split(cert_opt.tag == "Some")
with cert_br:
    cert = cert_opt.match(
        Some=lambda c: c,
        None_=lambda: mk_certificate(view, block, Set(str), id, Val(NOTARIZATION_KIND)),
    )
    process_certificate(c, id, cert, is_new_cert)
with no_cert_br:
    pass  # quorum not yet reached
```

The `None_` lambda in `.match()` is semantically unreachable inside `cert_br`,
but must be present for the type system: all branches must return the same
sort.

**Critical rule**: both branches of `c.split()` must be entered with a `with`
block — even if one branch is a no-op (`pass`). Omitting a branch silently
makes the split one-sided.

### 5.8 Conditional state changes inside a single action

When an action conditionally modifies a variable (no split desired), use
`Ite()` directly as the assigned value:

```python
s.sent_vote = Ite(
    should_send_notarize_vote,
    s.sent_vote | Set(mk_notarize(cert.view, id, cert.block)),
    s.sent_vote,
)
```

Use `c.split()` when the two branches have substantially different update
patterns; use `Ite()` when you can express both outcomes as a single
expression.

## 6. Invariants and properties

Keep invariants as pure functions over state decorated with `@invariant`:

```python
@invariant
def agreement(s: MinimmitState) -> BoolExpr:
    def check(id1: Expr, id2: Expr) -> BoolExpr:
        b1 = s.ghost_committed_blocks[id1]
        b2 = s.ghost_committed_blocks[id2]
        n = b1.size.min(b2.size)
        return b1[:n] == b2[:n]
    return Forall(check(id1, id2) for id1 in s.CORRECT for id2 in s.CORRECT)
```

For complex quantified invariants, define a local `check` function and fold it
with `Forall(...)` or `s.SOME_SET.forall(lambda x: ...)`.

Add a `@coverage` predicate (a tuple of the mutable variables) to steer random
simulation, and run/check through the `@instance` factory — **always with
`--timeout`** so CI cannot run unbounded:

```python
@coverage
def state_cov(s: MinimmitState) -> Expr:
    return Tuple(s.replica_state, s.sent_vote, s.sent_proposal)
```

```bash
wunderspec run examples/minimmit.py --instance n6_t1_f0 \
    --property agreement --max-samples 100 --max-steps 20 --timeout 30
wunderspec check examples/minimmit.py --instance n6_t1_f0 \
    --property agreement --max-steps 6 --timeout 30
```

If a Quint spec is unbounded, add `Param` caps (e.g. `MaxView`) and guard the
space-growing actions with `c.assume(s.view[id] < s.MaxView)` for bounded model
checking; document them as the only deviations from the source.

## 7. Helper function conventions

- Put `s: MyState` as the **first** argument of every helper that references state.
- Pure helpers that do not reference state receive only their own arguments.
- Constructor helpers (e.g., `mk_vote(...)`, `mk_proposal(...)`) need not take
  state — pass concrete expressions directly.

```python
# state-free constructor
def mk_notarize(view: Expr, id: Expr, block: Expr) -> Expr:
    return Vote(view=view, sig=id, block=block, kind=Val(NOTARIZE_KIND))

# state-dependent helper — state goes first
def is_view_notarized_votes(
    s: MinimmitState, view: Expr, votes: Expr, block: Expr
) -> BoolExpr:
    return select_votes(view, Val(NOTARIZE_KIND), votes) \
        .filter(lambda v: v.block == block) \
        .map(lambda v: v.sig) \
        .size >= M_quorum(s)
```

## 8. Naming and style conventions

- Quint `camelCase` identifiers → Python `snake_case`.
- Quint `SCREAMING_SNAKE` constants → Python module-level constants (no change needed).
- Keep Quint record field names as-is where they are already readable.
- Quint `init`/`step` → Python `init`/`step` (unchanged).
- Keep comments that explain protocol intent; skip syntactic trivia.
- Use explicit imports (`from wunderspec import A, B, C`).
- Inline imported Quint modules at the top of the Python file, separated by
  banner comments.

## 9. Common pitfalls

- Do not use Python `and`/`or`/`not` with Wunderspec expressions; use `And`,
  `Or`, `~`.
- Use `@expr` for symbolic helper functions that should be emitted as
  expression definitions; reserve undecorated helpers for ordinary Python
  scaffolding.
- Do not forget `c.assume(...)` for action guards. Omitting a guard
  over-approximates enabled actions.
- Both branches of `c.split()` must be entered — including no-op branches
  (`with no_cert_br: pass`).
- Prefer **direct assignment** for state updates: `s.m[k] = v`,
  `s.rec.field = v`, `s.m[i][j] = v`. Reserve `.replace`/`.insert`/`.edit` for
  building a value not bound to a state path (a message, a return value, an
  `Ite` branch). Translate `map.set(k, v)` writing to state as `s.m[k] = v`, not
  `s.m = s.m.replace(k, v)`.
- `m.replace(k, v)` is replace-only (key must exist); use `m.insert(k, v)` to add
  a key, and `StateVar[dict[K, V], UPSERT]` for state maps that grow.
- `expr.edit()` returns a builder whose updated value is `upd.result` — for a
  *pure* multi-field/nested record value, not for state (assign state directly).
- A keyed assignment to a state map field is `replace_only` by default; declare
  the field as `StateVar[dict[K, V], UPSERT]` for insert-or-update behavior.
- Use `with s.editing() as upd:` only when several updates must read the
  pre-update state atomically.
- `Ite` takes three arguments: `Ite(condition, then_value, else_value)`.
- Quint's `nondet x = S.oneOf()` picks one element; in Wunderspec use
  `c.one_of(S, "x")` — do not use `S.exists(...)` inside an action for this.
- Quint's `nondet senders = S.powerset().oneOf()` picks a *subset*; use
  `c.one_of(AllSubsets(S), "senders")`. Do not simplify to `c.one_of(S, ...)`
  even when the downstream `senders.map(_ => constant)` collapses to 0 or 1
  elements — the empty-set case (0 elements) is part of the spec.
- For parameterized specs (e.g., `CORRECT`, `REPLICA_KEYS`), define an
  `@instance` factory and run it with `--instance NAME`.

## 10. Minimmit mapping example

### `const`/`var` → `@state`

Quint:
```quint
const N: int
const CORRECT: Set[ReplicaId]
var replica_state: ReplicaId -> ReplicaState
var sent_vote: Set[Vote]
```

Wunderspec:
```python
@state
class MinimmitState(MachineStateBase):
    N: Param[int]
    CORRECT: Param[set[str]]
    replica_state: StateVar[dict[str, ReplicaStateRec]]
    sent_vote: StateVar[set[Vote]]
```

### Action with guards and nested update

Quint:
```quint
action on_proposal(id, proposal) = all {
  not(has_notarized(replica_state.get(id))),
  not(replica_state.get(id).nullified),
  proposal.block != DUMMY_BLOCK,
  proposal.view == replica_state.get(id).view,
  sent_vote' = sent_vote.union(Set(notarize(proposal.view, id, proposal.block))),
  replica_state' = replica_state.set(id,
    { ...replica_state.get(id), notarized: replica_state.get(id).notarized.set(proposal.view, proposal.block) }
  ),
}
```

Wunderspec:
```python
@action(inline=False)
def on_proposal(c: Context[MinimmitState], id: Expr, proposal: Expr):
    s = c.state
    self_rec = s.replica_state[id]

    c.assume(~has_notarized(self_rec))
    c.assume(~self_rec.nullified)
    c.assume(proposal.block != Val(DUMMY_BLOCK))
    c.assume(proposal.view == self_rec.view)

    s.sent_vote = s.sent_vote | Set(mk_notarize(proposal.view, id, proposal.block))
    s.replica_state = s.replica_state.replace(
        id,
        self_rec.replace(
            notarized=self_rec.notarized.replace(self_rec.view, proposal.block)
        ),
    )
```

### `nondet` + `any` in `step`

Quint:
```quint
action step = any {
  nondet id = CORRECT.oneOf()
  proposer_step(id),
  correct_replica_step,
  byzantine_replica_step,
}
```

Wunderspec:
```python
@action
def step(c: Context[MinimmitState]):
    s = c.state
    alts = iter(c.alternatives("ProposerStep", "CorrectStep", "ByzantineStep"))
    with next(alts):
        with c.one_of(s.CORRECT, "id") as id:
            ...  # proposer_step(c, id, ...)
    with next(alts):
        correct_replica_step(c)
    with next(alts):
        byzantine_replica_step(c)
```

## 11. Further reading and open TODOs

Worked translations: `examples/minimmit.py` (this guide's running example),
`examples/etcdraft.py` (union messages + multiset bags + sequences), and
`examples/bags.py` (the `Bag` ADT used for multiset state).

Still open:

- Quint `temporal` properties and fairness operators (`WeakFair`/`StrongFair`
  + `@temporal`, checked via `wunderspec with-tlc`/`with-apalache`).
- Quint `run` blocks (counter-example traces → `@example`).
- Quint module instantiation and `import ... from`.
- Quint `assume` at module level vs. action-level `c.assume`.
