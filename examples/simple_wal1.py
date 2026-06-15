"""
A specification of a simple database log. This the first version that is
not durable! With this specification, we demonstrate that it's not necessary
to reboot your computer to see the issues with durability.

Igor Konnov, 2026.

This is example is modeled after:

Justin Jaffray. Durability and Redo Logging, 2022.

https://justinjaffray.com/durability-and-redo-logging/
"""

from enum import Enum, auto

from wunderspec.expr import Expr, UnionExpr
from wunderspec.lang import Exists, List, Set, Tuple, Unit, Val, Variant, union
from wunderspec.machine import (
    Context,
    MachineStateBase,
    Param,
    StateVar,
    action,
    example,
    instance,
    invariant,
    state,
)


class PC(Enum):
    Start = auto()
    Wait = auto()
    Sync = auto()


@union
class Command:
    """A command for the database."""

    Nop: Variant[Unit]  # no pending command
    Set: Variant[tuple[int, int]]  # set a key to a value
    Delete: Variant[int]  # delete a key


@state
class KvStoreState(MachineStateBase):
    """The global state of the state machine."""

    KEYS: Param[set[int]]  # the set of potential keys in the database
    VALUES: Param[set[int]]  # the set of potential values in the database
    LOG_BOUND: Param[int]  # the upper bound on the log size
    pc: StateVar[PC]  # the program counter for the single process
    pending: StateVar[Command]  # the command waiting to be synced and applied
    kv_mem: StateVar[dict[int, int]]  # the current in-memory key/value mapping
    log: StateVar[list[Command]]  # the log of commands that have been executed
    log_synced: StateVar[
        int
    ]  # the index of the last log entry that has been synced to disk


def max_(a: Expr, b: Expr) -> Expr:
    """A helper function to compute the maximum of two integers."""
    return a.if_(a > b).else_(b)


def apply_command(kv: Expr, cmd: Expr) -> Expr:
    """Apply one log command to a key/value map."""
    command = UnionExpr(cmd.node)
    return command.match(
        Nop=lambda: kv,
        Set=lambda entry: kv.replace(entry[0], entry[1]),
        Delete=lambda key: kv.replace(key, Val(0)),
    )


def kv_from_log(s: KvStoreState) -> Expr:
    """Replay the log into a key/value map."""
    return s.log.reduce(apply_command, s.KEYS.map_to(lambda _: Val(0)))


@action(init=True)
def init(c: Context[KvStoreState]):
    s = c.state
    s.pc = Val(PC.Start)
    s.pending = Command.Nop()  # type: ignore[operator]
    s.kv_mem = s.KEYS.map_to(lambda _: Val(0))
    s.log = List(Command)
    s.log_synced = Val(0)


@action(inline=True)
def kv_step(c: Context[KvStoreState]):
    s = c.state
    start, set_, delete, sync = iter(c.alternatives("Start", "Set", "Delete", "Sync"))
    with start:
        c.assume(s.pc == PC.Start)
        # TODO: the process has just started or restarted after a crash.
        # We have to recover the state of the database by replaying the log.
        # See simple_wal2.py.
        s.pc = Val(PC.Wait)
    with set_:
        c.assume(s.pc == PC.Wait)
        c.assume(s.log.size < s.LOG_BOUND)  # do not grow beyond the bound
        with c.one_of(s.KEYS, "key") as key, c.one_of(s.VALUES, "value") as value:
            # Store the command. It is logged and applied only in PC.Sync.
            s.pending = Command.Set(Tuple(key, value))  # type: ignore[operator]
            s.pc = Val(PC.Sync)
    with delete:
        c.assume(s.pc == PC.Wait)
        c.assume(s.log.size < s.LOG_BOUND)  # do not grow beyond the bound
        with c.one_of(s.KEYS, "key") as key:
            # Store the command. It is logged and applied only in PC.Sync.
            s.pending = Command.Delete(key)  # type: ignore[operator]
            s.pc = Val(PC.Sync)
    with sync:
        c.assume(s.pc == PC.Sync)
        next_log = s.log + List(s.pending)
        s.log_synced = max_(s.log_synced, next_log.size)
        s.log = next_log
        s.kv_mem = apply_command(s.kv_mem, s.pending)
        s.pending = Command.Nop()  # type: ignore[operator]
        s.pc = Val(PC.Wait)


@action
def step(c: Context[KvStoreState]):
    run, crash = c.alternatives("Run", "Restart")
    with run:
        kv_step(c)
    with crash:
        # the process crashes and restarts, which resets the program counter to Start.
        # note that the log and log_synced are not reset, which models durability.
        s = c.state
        s.pc = Val(PC.Start)
        s.pending = Command.Nop()  # type: ignore[operator]
        s.kv_mem = s.KEYS.map_to(lambda _: Val(0))


@example
def non_empty_log(s: KvStoreState):
    """Produce an example of a non-empty log."""
    return s.log.size > 0


@example
def non_zero_value(s: KvStoreState):
    """Produce an example of a kv-store having a non-zero value."""
    return Exists(s.kv_mem[k] != 0 for k in s.kv_mem.keys)


@invariant
def kv_mem_matches_log(s: KvStoreState):
    """The key invariant."""
    return (s.kv_mem == kv_from_log(s)) | (s.pc == PC.Start)


@instance
def tiny2() -> KvStoreState:
    """A tiny instance of two keys and two values"""
    return KvStoreState(KEYS=Set(1, 2), VALUES=Set(4, 5), LOG_BOUND=5)


@instance
def small3() -> KvStoreState:
    """A very small instance of three keys and three values"""
    return KvStoreState(KEYS=Set(1, ..., 3), VALUES=Set(4, ..., 6), LOG_BOUND=10)
