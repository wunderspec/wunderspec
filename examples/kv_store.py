"""
Key-value store with snapshot isolation in Wunderspec.

Translation of:
https://github.com/tlaplus/Examples/blob/master/specifications/KeyValueStore/KeyValueStore.tla
"""

from wunderspec import (
    AllMaps,
    AllSubsets,
    And,
    BoolExpr,
    Expr,
    Param,
    Set,
    StateVar,
    Tuple,
    Val,
)
from wunderspec.machine import (
    Context,
    MachineStateBase,
    action,
    coverage,
    instance,
    invariant,
    state,
)

NO_VAL = Val(-1)


@state
class KVStoreState(MachineStateBase):
    # TLA+ constants
    Key: Param[set[str]]
    Value: Param[set[int]]
    TxId: Param[set[int]]

    # TLA+ variables
    store: StateVar[dict[str, int]]
    tx: StateVar[set[int]]
    snapshot_store: StateVar[dict[int, dict[str, int]]]
    written: StateVar[dict[int, set[str]]]
    missed: StateVar[dict[int, set[str]]]


@action(inline=False)
def open_tx(c: Context[KVStoreState], t: Expr):
    s = c.state
    c.assume(~s.tx.contains(t))
    s.tx = s.tx | Set(t)
    s.snapshot_store = s.snapshot_store.replace(t, s.store)


@action(inline=False)
def add(c: Context[KVStoreState], t: Expr, k: Expr, v: Expr):
    s = c.state
    c.assume(s.tx.contains(t))
    c.assume(s.snapshot_store[t][k] == NO_VAL)
    s.snapshot_store = s.snapshot_store.replace(t, s.snapshot_store[t].replace(k, v))
    s.written = s.written.replace(t, s.written[t] | Set(k))


@action(inline=False)
def update(c: Context[KVStoreState], t: Expr, k: Expr, v: Expr):
    s = c.state
    c.assume(s.tx.contains(t))
    c.assume(~Set(NO_VAL, v).contains(s.snapshot_store[t][k]))
    s.snapshot_store = s.snapshot_store.replace(t, s.snapshot_store[t].replace(k, v))
    s.written = s.written.replace(t, s.written[t] | Set(k))


@action(inline=False)
def remove(c: Context[KVStoreState], t: Expr, k: Expr):
    s = c.state
    c.assume(s.tx.contains(t))
    c.assume(s.snapshot_store[t][k] != NO_VAL)
    s.snapshot_store = s.snapshot_store.replace(
        t, s.snapshot_store[t].replace(k, NO_VAL)
    )
    s.written = s.written.replace(t, s.written[t] | Set(k))


@action(inline=False)
def rollback_tx(c: Context[KVStoreState], t: Expr):
    s = c.state
    c.assume(s.tx.contains(t))
    s.tx = s.tx - Set(t)
    s.snapshot_store = s.snapshot_store.replace(t, s.Key.map_to(lambda _: NO_VAL))
    s.written = s.written.replace(t, Set(str))
    s.missed = s.missed.replace(t, Set(str))


@action(inline=False)
def close_tx(c: Context[KVStoreState], t: Expr):
    s = c.state
    c.assume(s.tx.contains(t))
    c.assume((s.missed[t] & s.written[t]).is_empty)
    s.store = s.Key.map_to(
        lambda k: s.snapshot_store[t][k].if_(s.written[t].contains(k)).else_(s.store[k])
    )
    s.tx = s.tx - Set(t)
    s.missed = s.TxId.map_to(
        lambda other_tx: (s.missed[other_tx] | s.written[t])
        .if_(s.tx.contains(other_tx))
        .else_(Set(str))
    )
    s.snapshot_store = s.snapshot_store.replace(t, s.Key.map_to(lambda _: NO_VAL))
    s.written = s.written.replace(t, Set(str))


@action(init=True)
def init(c: Context[KVStoreState]):
    s = c.state
    s.store = s.Key.map_to(lambda _: NO_VAL)
    s.tx = Set(int)
    s.snapshot_store = s.TxId.map_to(lambda _: s.Key.map_to(lambda _: NO_VAL))
    s.written = s.TxId.map_to(lambda _: Set(str))
    s.missed = s.TxId.map_to(lambda _: Set(str))


@action
def step(c: Context[KVStoreState]):
    s = c.state
    alts = iter(
        c.alternatives("OpenTx", "Add", "Update", "Remove", "RollbackTx", "CloseTx")
    )

    with next(alts), c.one_of(s.TxId, "t") as t:
        open_tx(c, t)

    with (
        next(alts),
        c.one_of(s.tx, "t") as t,
        c.one_of(s.Key, "k") as k,
        c.one_of(s.Value, "v") as v,
    ):
        add(c, t, k, v)

    with (
        next(alts),
        c.one_of(s.tx, "t") as t,
        c.one_of(s.Key, "k") as k,
        c.one_of(s.Value, "v") as v,
    ):
        update(c, t, k, v)

    with next(alts), c.one_of(s.tx, "t") as t, c.one_of(s.Key, "k") as k:
        remove(c, t, k)

    with next(alts), c.one_of(s.tx, "t") as t:
        rollback_tx(c, t)

    with next(alts), c.one_of(s.tx, "t") as t:
        close_tx(c, t)


@invariant
def type_invariant(s: KVStoreState) -> BoolExpr:
    store_set = AllMaps(s.Key, s.Value | Set(NO_VAL))
    return And(
        # store \in Store, where Store == [Key -> Val \cup {NoVal}]
        store_set.contains(s.store),
        # tx \subseteq TxId
        s.tx <= s.TxId,
        # snapshotStore \in [TxId -> Store]
        AllMaps(s.TxId, store_set).contains(s.snapshot_store),
        # written \in [TxId -> SUBSET Key]
        AllMaps(s.TxId, AllSubsets(s.Key)).contains(s.written),
        # missed \in [TxId -> SUBSET Key]
        AllMaps(s.TxId, AllSubsets(s.Key)).contains(s.missed),
    )


@invariant
def tx_lifecycle(s: KVStoreState) -> BoolExpr:
    return And(
        s.tx.forall(
            lambda t: s.Key.forall(
                lambda k: (
                    (s.store[k] != s.snapshot_store[t][k]) & ~s.written[t].contains(k)
                ).implies(s.missed[t].contains(k))
            )
        ),
        (s.TxId - s.tx).forall(
            lambda t: And(
                s.Key.forall(lambda k: s.snapshot_store[t][k] == NO_VAL),
                s.written[t].is_empty,
                s.missed[t].is_empty,
            )
        ),
    )


@coverage
def state_cov(s: KVStoreState) -> Expr:
    return Tuple(s.store, s.tx, s.snapshot_store, s.written, s.missed)


@instance
def tiny() -> KVStoreState:
    return KVStoreState(
        Key=Set("a", "b"),
        Value=Set(0, 1),
        TxId=Set(1, 2),
    )


@instance
def small() -> KVStoreState:
    return KVStoreState(
        Key=Set("a", "b", "c"),
        Value=Set(0, 1),
        TxId=Set(1, 2, 3),
    )
