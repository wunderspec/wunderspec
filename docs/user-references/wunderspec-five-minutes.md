# Wunderspec in Five Minutes

Since nobody has time to read docs, here is a one-page summary of what you want
to know about Wunderspec. For more details, see the [Cheatsheet][cheatsheet].

**States and actions**. The most important definitions are annotated with
`@state` and `@action`. Wunderspec uses these definitions to define a *state
machine*.  This state machine starts in a state defined with
`@action(init=True)` and evolves via one or more actions labeled with `@action`.
Normally, the top-level actions are grouped with an action called `step`. All
actions are atomic -- either an action executes (when all assumptions are met),
and its effects are applied, or it doesn't execute at all. For a simple example,
see the [sloppy counter][sloppy_counter].

**Assumptions and assignments**. Actions normally contain assumptions about
the current state and assignments to the next state. Here is a typical example
from the [ledger][ledger]:

```python
@action
def start_transaction(c: Context[LedgerState], company: Annotated[Expr, str]):
    s = c.state
    c.assume(s.pending_seq_no[company] == -1)
    s.pending_seq_no[company] = s.registered_seq_no[company]
    s.registered_seq_no[company] += 1
```

The Wunderspec transpiler collects the assumptions and assignments, when the
Python code is executed. They are used to define the state machine.

**Every variable of a state machine may be assigned at most once in an action**.
Use local Python variables to compute intermediate values.

**Value generation**. State machines in Wunderspec often use
non-deterministically generated values. These values are produced with `with
c.one_of(...)`. We can see an example in the [sloppy counter][sloppy_counter]:

```python
@action
def step(c: Context[SloppyCounterState]):
    with c.one_of(cpus(c.state), "cpu") as cpu:
        increment(c, cpu)
```

In the code above, `cpu` receive a value from the set `cpus(c.state)`.  The
second argument to `one_of` is a convenient alias for the intermediate
representation and generated code. When it's omitted, the name is generated
automatically. Wunderspec commands interpret this construct differently:

 - `wunderspec run` generates values randomly,
 
 - `wunderspec check` generates values systematically to explore all possible
 executions,

 - `wunderspec with-apalache` produces values with the SMT solver Z3.

**Control non-determinism.** Wunderspec does not have explicit processes,
threads, processors, or other computing devices. Instead, is uses control
non-determinism to model choice of actions. Non-deterministic choice is done
with `with c.alternatives(...)`. For example, he is how [simple
WAL][simple_wal1] defines `step` with alternatives:

```python
@action
def step(c: Context[KvStoreState]):
    run, crash = c.alternatives("Run", "Restart")
    with run:
        kv_step(c)
    with crash:
        s = c.state
        s.pc = Val(PC.Start)
        s.pending = Command.Nop()
        s.kv_mem = s.KEYS.map_to(lambda _: Val(0))
```

In the above code, the call to `c.alternatives` introduces two branching
alternatives, "Run" and "Restart". Wunderspec commands interpret these
alternatives differently:

 - `wunderspec run` chooses one of the alternatives randomly,
 
 - `wunderspec check` explores all possible alternatives systematically,

 - `wunderspec with-apalache` explores the alternatives symbolically.

Similar to `c.alternatives`, we have a special version `c.split(condition)` that
introduces two alternatives, one where `condition` holds and another where it
doesn't hold.

**Properties.** Properties are the targets for analysis tools. Wunderspec
currently has three kinds of properties:

 - state `@invariant` is a Boolean condition over a state that must hold in all
 states reachable from the initial states. All tools check state invariants. If
 they find a state that violates the invariant, they report a counterexample
 execution.
 
 - state `@example` is a Boolean condition over a state that maybe holds in some
 execution of the state machine. Tools report one or more executions that lead
 to a state that satisfies the example.
 
 - `@temporal` properties must hold in all executions. They are typically used
 to check complex safety properties and liveness properties. Currently, only
 `with-tlc` and `with-apalache` check temporal properties.
 
 **Immutability.** While the Wunderspec state machines mutate the state,
 the expressions operate over immutable data structures. For the data
 structures, see the [Cheatsheet][cheatsheet].
 
 
[sloppy_counter]: https://github.com/wunderspec/wunderspec/blob/main/examples/sloppy_counter.py
[ledger]: https://github.com/wunderspec/wunderspec/blob/main/examples/ledger.py
[simple_wal1]: https://github.com/wunderspec/wunderspec/blob/main/examples/simple_wal1.py
[cheatsheet]: https://wunderspec.com/cheatsheet