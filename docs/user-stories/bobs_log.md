# Welcome Bob

Here is Bob. He is implementing a key-value store. Bob read the [blog
post][jaffray-post] by Justin Jaffray. Now, Bob wants to try these ideas
*without rebooting his computer*. Wunderspec is a good fit for that.

![Bob](img/bob.png)

## 1. Writing the first spec

Bob thinks about the most minimal specification that would capture the behavior
of a write-ahead log from the blog post. Instead of throwing an AI tool at the
problem, he thinks for 5 minutes and starts writing the specification. It is
actually small and cute.

The key decisions here is how to represent the state machine state and which
actions to allow for. We are not going to discuss these decisions here. Just
check [the specification][simple_wal1.py].

An extremely important feature is the conceptual separation between the
benign behavior and the faulty behavior in `step`:

```py
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
```

Hence, with `kv_step` Bob focuses on the nominal code behavior, whereas the
crash case in `step` captures the crash behavior precisely.

Bob already notices that writing the specification was useful on its own. It's
not hand-waving anymore but a precise description of the possible behaviors.

However, Bob is not sure about what to do after the restarts. Check the "TODO"
section in the specification. To this end, Bob decides to run the Wunderspec
tools.

## 2. Basic checks

Now, Bob wants to get the value from the Wunderspec tools. First, he checks
that the spec is in the right shape:

```sh
uv run wunderspec lint examples/simple_wal1.py
```

<!--pytest-codeblocks:expected-output-->

```text
info: Linting: examples/simple_wal1.py
success: No lint errors found
```

As the next step, Bob wants just to "run" the specification. Since our
specification can produce long computations, Bob restricts the computations to
10 steps:

```sh
DEFAULTS="--seed=3 --no-progress" # testing defaults
uv run wunderspec run --instance tiny2 --max-steps=10 examples/simple_wal1.py $DEFAULTS
``` 

<!--pytest-codeblocks:expected-output-->

```text
info: Seed: 3
Rerun the search with: wunderspec run --seed=3 --instance tiny2 --max-steps 10 --no-progress examples/simple_wal1.py
info: No --property provided; use --property to search for a property. Looking for the longest trace.
success: Explored 1000 samples without checking a predicate
Best trace seed: 2405875930906139466
Best trace length: 10
[State 0]
  KEYS: Set({1, 2})
  LOG_BOUND: 5
  VALUES: Set({4, 5})
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Start
  pending: Nop
[State 1]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Start
  pending: Nop
[State 2]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Wait
  pending: Nop
[State 3]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Sync
  pending: Set((1, 5))
[State 4]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Start
  pending: Nop
[State 5]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Wait
  pending: Nop
[State 6]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Start
  pending: Nop
[State 7]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Start
  pending: Nop
[State 8]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Wait
  pending: Nop
[State 9]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Sync
  pending: Delete(2)
Replay with: wunderspec replay --instance tiny2 --max-steps 10 examples/simple_wal1.py --seed 2405875930906139466
```

As we see, `wunderspec run` produced 1000 traces and printed one of the longest
traces. It's reassuring that the specification is doing something. However, Bob
is not happy about this trace: There are too many restarts, and the log is
empty.

## 3. Producing examples

Bob wants to see an example of an execution that adds at least one command
to the log. Hence, he writes an example:

```py
@example
def non_empty_log(s: KvStoreState):
    """Produce an example of a non-empty log."""
    return s.log.size > 0
```

With this example, Bob executes `wunderspec run` again, but this time, he
specifies the goal of producing such an example:

```sh
DEFAULTS="--seed=3 --no-progress" # testing defaults
uv run wunderspec run --instance tiny2 --max-steps=10 --property=non_empty_log examples/simple_wal1.py $DEFAULTS || test $? -eq 2
``` 

<!--pytest-codeblocks:expected-output-->

```text
info: Seed: 3
Rerun the search with: wunderspec run --seed=3 --instance tiny2 --property non_empty_log --max-steps 10 --no-progress examples/simple_wal1.py
Example found at state 3
Trace seed: 2940409807404031313
[State 0]
  KEYS: Set({1, 2})
  LOG_BOUND: 5
  VALUES: Set({4, 5})
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Start
  pending: Nop
[State 1]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Wait
  pending: Nop
[State 2]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Sync
  pending: Delete(2)
[State 3]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: [Delete(2)]
  log_synced: 1
  pc: PC.Wait
  pending: Nop
Replay with: wunderspec replay --instance tiny2 --property non_empty_log --max-steps 10 examples/simple_wal1.py --seed 2940409807404031313
info: Found 1 example trace(s) in 10 samples
```

This is cool! Now, Bob decides to look at an example that updates the key-value
store. So he adds this example:

```py
@example
def non_zero_value(s: KvStoreState):
    """Produce an example of a kv-store having a non-zero value."""
    return Exists(s.kv_mem[k] != 0 for k in s.kv_mem.keys)
```

This is how he produces this example:

```sh
DEFAULTS="--seed=3 --no-progress" # testing defaults
uv run wunderspec run --instance tiny2 --max-steps=10 --property=non_zero_value examples/simple_wal1.py $DEFAULTS || test $? -eq 2
``` 

<!--pytest-codeblocks:expected-output-->

```text
info: Seed: 3
Rerun the search with: wunderspec run --seed=3 --instance tiny2 --property non_zero_value --max-steps 10 --no-progress examples/simple_wal1.py
Example found at state 4
Trace seed: 5379299308513165481
[State 0]
  KEYS: Set({1, 2})
  LOG_BOUND: 5
  VALUES: Set({4, 5})
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Start
  pending: Nop
[State 1]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Start
  pending: Nop
[State 2]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Wait
  pending: Nop
[State 3]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Sync
  pending: Set((1, 5))
[State 4]
  kv_mem: Map(1 -> 5, 2 -> 0)
  log: [Set((1, 5))]
  log_synced: 1
  pc: PC.Wait
  pending: Nop
Replay with: wunderspec replay --instance tiny2 --property non_zero_value --max-steps 10 examples/simple_wal1.py --seed 5379299308513165481
info: Found 1 example trace(s) in 44 samples
```

## 4. Breaking the invariant

Now, Bob sees that the specification does something useful. However, does it
always work as expected? To check this, Bob write the following state invariant:

```py
@invariant
def kv_mem_matches_log(s: KvStoreState):
    """The key invariant. It is broken in this version."""
    return s.kv_mem == kv_from_log(s)
```

Bob executes `wunderspec run` to check the invariant against a small number of
runs:

```sh
DEFAULTS="--seed=3 --no-progress" # testing defaults
uv run wunderspec run --instance tiny2 --max-steps=10 --property=kv_mem_matches_log examples/simple_wal1.py $DEFAULTS || test $? -eq 1
``` 

<!--pytest-codeblocks:expected-output-->

```text
info: Seed: 3
Rerun the search with: wunderspec run --seed=3 --instance tiny2 --property kv_mem_matches_log --max-steps 10 --no-progress examples/simple_wal1.py
Invariant violation at state 9
Trace seed: 596164577152659537
[State 0]
  KEYS: Set({1, 2})
  LOG_BOUND: 5
  VALUES: Set({4, 5})
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Start
  pending: Nop
[State 1]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Start
  pending: Nop
[State 2]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Start
  pending: Nop
[State 3]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Wait
  pending: Nop
[State 4]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Sync
  pending: Delete(2)
[State 5]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: [Delete(2)]
  log_synced: 1
  pc: PC.Wait
  pending: Nop
[State 6]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: [Delete(2)]
  log_synced: 1
  pc: PC.Sync
  pending: Set((2, 4))
[State 7]
  kv_mem: Map(1 -> 0, 2 -> 4)
  log: [Delete(2), Set((2, 4))]
  log_synced: 2
  pc: PC.Wait
  pending: Nop
[State 8]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: [Delete(2), Set((2, 4))]
  log_synced: 2
  pc: PC.Start
  pending: Nop
[State 9]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: [Delete(2), Set((2, 4))]
  log_synced: 2
  pc: PC.Wait
  pending: Nop
Replay with: wunderspec replay --instance tiny2 --property kv_mem_matches_log --max-steps 10 examples/simple_wal1.py --seed 596164577152659537
```

## 5. Replaying the invariant violation

The invariant `kv_mem_matches_log` is violated on [simple_wal1.py][]. How does
Bob understand the error? He has several options:

 1. The old-school Bob stares at the sequence of printed states.

 1. The vibe-coding Bob feeds the trace into an AI tool and asks it to explain.

 1. The curious Bob uses `wunderspec replay` and goes over the trace
 step-by-step.

Here is how the curious Bob replays the trace by using the printed trace seed:

```sh
uv run wunderspec replay --instance tiny2 --property kv_mem_matches_log \
  examples/simple_wal1.py --seed 596164577152659537 || test $? -eq 1
``` 

<!--pytest-codeblocks:expected-output-->

```text
Trace seed: 596164577152659537
Trace length: 20
Action trace:
  [Step 0]
    examples/simple_wal1.py:86:4 assign
    examples/simple_wal1.py:87:4 assign
    examples/simple_wal1.py:88:4 assign
    examples/simple_wal1.py:89:4 assign
    examples/simple_wal1.py:90:4 assign
    [State 0]
      KEYS: Set({1, 2})
      LOG_BOUND: 5
      VALUES: Set({4, 5})
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: []
      log_synced: 0
      pc: PC.Start
      pending: Nop
  [Step 1]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:132:4 and
    examples/simple_wal1.py:136:8 assign
    examples/simple_wal1.py:137:8 assign
    examples/simple_wal1.py:138:8 assign
    [State 1]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: []
      log_synced: 0
      pc: PC.Start
      pending: Nop
  [Step 2]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:132:4 and
    examples/simple_wal1.py:136:8 assign
    examples/simple_wal1.py:137:8 assign
    examples/simple_wal1.py:138:8 assign
    [State 2]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: []
      log_synced: 0
      pc: PC.Start
      pending: Nop
  [Step 3]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:117:4 alt
    examples/simple_wal1.py:97:4 and
    examples/simple_wal1.py:98:8 assume
    examples/simple_wal1.py:102:8 assign
    [State 3]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: []
      log_synced: 0
      pc: PC.Wait
      pending: Nop
  [Step 4]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:117:4 alt
    examples/simple_wal1.py:110:4 and
    examples/simple_wal1.py:111:8 assume
    examples/simple_wal1.py:112:8 assume
    examples/simple_wal1.py:113:8 and
    examples/simple_wal1.py:113:8 assume
    examples/simple_wal1.py:113:8 one_of
    examples/simple_wal1.py:113:8 and
    examples/simple_wal1.py:115:12 assign
    examples/simple_wal1.py:116:12 assign
    [State 4]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: []
      log_synced: 0
      pc: PC.Sync
      pending: Delete(2)
  [Step 5]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:117:4 alt
    examples/simple_wal1.py:117:4 and
    examples/simple_wal1.py:118:8 assume
    examples/simple_wal1.py:120:8 assign
    examples/simple_wal1.py:121:8 assign
    examples/simple_wal1.py:122:8 assign
    examples/simple_wal1.py:123:8 assign
    examples/simple_wal1.py:124:8 assign
    [State 5]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: [Delete(2)]
      log_synced: 1
      pc: PC.Wait
      pending: Nop
  [Step 6]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:117:4 alt
    examples/simple_wal1.py:103:4 and
    examples/simple_wal1.py:104:8 assume
    examples/simple_wal1.py:105:8 assume
    examples/simple_wal1.py:106:8 and
    examples/simple_wal1.py:106:8 assume
    examples/simple_wal1.py:106:8 one_of
    examples/simple_wal1.py:106:8 and
    examples/simple_wal1.py:106:8 assume
    examples/simple_wal1.py:106:8 one_of
    examples/simple_wal1.py:106:8 and
    examples/simple_wal1.py:108:12 assign
    examples/simple_wal1.py:109:12 assign
    [State 6]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: [Delete(2)]
      log_synced: 1
      pc: PC.Sync
      pending: Set((2, 4))
  [Step 7]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:117:4 alt
    examples/simple_wal1.py:117:4 and
    examples/simple_wal1.py:118:8 assume
    examples/simple_wal1.py:120:8 assign
    examples/simple_wal1.py:121:8 assign
    examples/simple_wal1.py:122:8 assign
    examples/simple_wal1.py:123:8 assign
    examples/simple_wal1.py:124:8 assign
    [State 7]
      kv_mem: Map(1 -> 0, 2 -> 4)
      log: [Delete(2), Set((2, 4))]
      log_synced: 2
      pc: PC.Wait
      pending: Nop
  [Step 8]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:132:4 and
    examples/simple_wal1.py:136:8 assign
    examples/simple_wal1.py:137:8 assign
    examples/simple_wal1.py:138:8 assign
    [State 8]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: [Delete(2), Set((2, 4))]
      log_synced: 2
      pc: PC.Start
      pending: Nop
  [Step 9]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:117:4 alt
    examples/simple_wal1.py:97:4 and
    examples/simple_wal1.py:98:8 assume
    examples/simple_wal1.py:102:8 assign
    [State 9]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: [Delete(2), Set((2, 4))]
      log_synced: 2
      pc: PC.Wait
      pending: Nop
Invariant violation at state 9
```

The cool thing is that an IDE like Visual Studio Code allows Bob to **click on
the printed source locations** and see the parts of the code that led to the
changes in the trace. Bob goes forwards and backwards in the trace. No
additional debugger is needed!

## 6. Fixing the specification

By looking at the counterexample, Bob understands that the recovery from the log
is the source of invariant violation. This is exactly the issue that Bob as left
in his TODO!  He fixes the specification in [simple_wal2.py][]. This time, Bob
implements recovery from the persistent log.

```sh
DEFAULTS="--seed=10 --no-progress" # testing defaults
uv run wunderspec run --instance tiny2 --property kv_mem_matches_log \
  --max-samples=10000 --max-steps=10 examples/simple_wal2.py $DEFAULTS
``` 

<!--pytest-codeblocks:expected-output-->

```text
info: Seed: 10
Rerun the search with: wunderspec run --seed=10 --instance tiny2 --property kv_mem_matches_log --max-samples 10000 --max-steps 10 --no-progress examples/simple_wal2.py
success: No invariant violations in 10000 samples
```

## 7. Checking the invariant by enumeration

Bob has checked the invariant with `wunderspec run` and found no violations.
However, he read Igor Konnov's [blog post on random
simulations][random-simulations]. So Bob is careful. He runs `wunderspec check`
to exhaustively enumerate all the states of this specification under the tiny
scope:


```sh
DEFAULTS="--seed=10 --no-progress --no-color" # testing defaults
uv run wunderspec check --instance tiny2 --property kv_mem_matches_log \
  examples/simple_wal2.py $DEFAULTS
``` 

<!--pytest-codeblocks:expected-output-->

```text
info: Shuffling with seed: 10
success: No invariant violations found (55984 states produced, 27992 distinct)
```

Even this tiny configuration had about 28 thousand distinct states.  When Bob
wants to check a larger configuration, he replaces `tiny2` with `small3`.
This takes longer, so we skip it.

## 8. Finding examples by enumeration

Just to make sure that `wunderspec check` finds similar examples and replays
them, Bob tries this command:

```sh
DEFAULTS="--seed=12 --no-progress --no-color --out-schedule=s.json" # testing defaults
uv run wunderspec check --instance tiny2  \
  --property kv_mem_matches_log examples/simple_wal1.py $DEFAULTS || test $? -eq 1
``` 

This is what Bob sees:

<!--pytest-codeblocks:expected-output-->

```text
info: Shuffling with seed: 12
Invariant violation found (46 states produced, 24 distinct)
[State 0]
  KEYS: Set({1, 2})
  LOG_BOUND: 5
  VALUES: Set({4, 5})
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Start
  pending: Nop
[State 1]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Wait
  pending: Nop
[State 2]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: []
  log_synced: 0
  pc: PC.Sync
  pending: Delete(1)
[State 3]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: [Delete(1)]
  log_synced: 1
  pc: PC.Wait
  pending: Nop
[State 4]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: [Delete(1)]
  log_synced: 1
  pc: PC.Sync
  pending: Delete(1)
[State 5]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: [Delete(1), Delete(1)]
  log_synced: 2
  pc: PC.Wait
  pending: Nop
[State 6]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: [Delete(1), Delete(1)]
  log_synced: 2
  pc: PC.Sync
  pending: Delete(1)
[State 7]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: [Delete(1), Delete(1), Delete(1)]
  log_synced: 3
  pc: PC.Wait
  pending: Nop
[State 8]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: [Delete(1), Delete(1), Delete(1)]
  log_synced: 3
  pc: PC.Sync
  pending: Delete(1)
[State 9]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: [Delete(1), Delete(1), Delete(1), Delete(1)]
  log_synced: 4
  pc: PC.Wait
  pending: Nop
[State 10]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: [Delete(1), Delete(1), Delete(1), Delete(1)]
  log_synced: 4
  pc: PC.Sync
  pending: Set((1, 4))
[State 11]
  kv_mem: Map(1 -> 4, 2 -> 0)
  log: [Delete(1), Delete(1), Delete(1), Delete(1), Set((1, 4))]
  log_synced: 5
  pc: PC.Wait
  pending: Nop
[State 12]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: [Delete(1), Delete(1), Delete(1), Delete(1), Set((1, 4))]
  log_synced: 5
  pc: PC.Start
  pending: Nop
[State 13]
  kv_mem: Map(1 -> 0, 2 -> 0)
  log: [Delete(1), Delete(1), Delete(1), Delete(1), Set((1, 4))]
  log_synced: 5
  pc: PC.Wait
  pending: Nop
Replay with: wunderspec replay --instance tiny2 --property kv_mem_matches_log examples/simple_wal1.py --from-schedule s.json
```

## 9. Replaying schedules from enumeration

Now, Bob replays the generated schedule in a format, where he can click through
the code:

```sh
DEFAULTS="--no-color" # testing defaults
uv run wunderspec replay --instance tiny2 \
  --property kv_mem_matches_log examples/simple_wal1.py --from-schedule s.json \
  $DEFAULTS || test $? -eq 1
``` 

This is what Bob sees:

<!--pytest-codeblocks:expected-output-->

```text
Trace seed: from schedule
Trace length: 14
Action trace:
  [Step 0]
    examples/simple_wal1.py:86:4 assign
    examples/simple_wal1.py:87:4 assign
    examples/simple_wal1.py:88:4 assign
    examples/simple_wal1.py:89:4 assign
    examples/simple_wal1.py:90:4 assign
    [State 0]
      KEYS: Set({1, 2})
      LOG_BOUND: 5
      VALUES: Set({4, 5})
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: []
      log_synced: 0
      pc: PC.Start
      pending: Nop
  [Step 1]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:117:4 alt
    examples/simple_wal1.py:97:4 and
    examples/simple_wal1.py:98:8 assume
    examples/simple_wal1.py:102:8 assign
    [State 1]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: []
      log_synced: 0
      pc: PC.Wait
      pending: Nop
  [Step 2]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:117:4 alt
    examples/simple_wal1.py:110:4 and
    examples/simple_wal1.py:111:8 assume
    examples/simple_wal1.py:112:8 assume
    examples/simple_wal1.py:113:8 and
    examples/simple_wal1.py:113:8 assume
    examples/simple_wal1.py:113:8 one_of
    examples/simple_wal1.py:113:8 and
    examples/simple_wal1.py:115:12 assign
    examples/simple_wal1.py:116:12 assign
    [State 2]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: []
      log_synced: 0
      pc: PC.Sync
      pending: Delete(1)
  [Step 3]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:117:4 alt
    examples/simple_wal1.py:117:4 and
    examples/simple_wal1.py:118:8 assume
    examples/simple_wal1.py:120:8 assign
    examples/simple_wal1.py:121:8 assign
    examples/simple_wal1.py:122:8 assign
    examples/simple_wal1.py:123:8 assign
    examples/simple_wal1.py:124:8 assign
    [State 3]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: [Delete(1)]
      log_synced: 1
      pc: PC.Wait
      pending: Nop
  [Step 4]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:117:4 alt
    examples/simple_wal1.py:110:4 and
    examples/simple_wal1.py:111:8 assume
    examples/simple_wal1.py:112:8 assume
    examples/simple_wal1.py:113:8 and
    examples/simple_wal1.py:113:8 assume
    examples/simple_wal1.py:113:8 one_of
    examples/simple_wal1.py:113:8 and
    examples/simple_wal1.py:115:12 assign
    examples/simple_wal1.py:116:12 assign
    [State 4]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: [Delete(1)]
      log_synced: 1
      pc: PC.Sync
      pending: Delete(1)
  [Step 5]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:117:4 alt
    examples/simple_wal1.py:117:4 and
    examples/simple_wal1.py:118:8 assume
    examples/simple_wal1.py:120:8 assign
    examples/simple_wal1.py:121:8 assign
    examples/simple_wal1.py:122:8 assign
    examples/simple_wal1.py:123:8 assign
    examples/simple_wal1.py:124:8 assign
    [State 5]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: [Delete(1), Delete(1)]
      log_synced: 2
      pc: PC.Wait
      pending: Nop
  [Step 6]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:117:4 alt
    examples/simple_wal1.py:110:4 and
    examples/simple_wal1.py:111:8 assume
    examples/simple_wal1.py:112:8 assume
    examples/simple_wal1.py:113:8 and
    examples/simple_wal1.py:113:8 assume
    examples/simple_wal1.py:113:8 one_of
    examples/simple_wal1.py:113:8 and
    examples/simple_wal1.py:115:12 assign
    examples/simple_wal1.py:116:12 assign
    [State 6]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: [Delete(1), Delete(1)]
      log_synced: 2
      pc: PC.Sync
      pending: Delete(1)
  [Step 7]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:117:4 alt
    examples/simple_wal1.py:117:4 and
    examples/simple_wal1.py:118:8 assume
    examples/simple_wal1.py:120:8 assign
    examples/simple_wal1.py:121:8 assign
    examples/simple_wal1.py:122:8 assign
    examples/simple_wal1.py:123:8 assign
    examples/simple_wal1.py:124:8 assign
    [State 7]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: [Delete(1), Delete(1), Delete(1)]
      log_synced: 3
      pc: PC.Wait
      pending: Nop
  [Step 8]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:117:4 alt
    examples/simple_wal1.py:110:4 and
    examples/simple_wal1.py:111:8 assume
    examples/simple_wal1.py:112:8 assume
    examples/simple_wal1.py:113:8 and
    examples/simple_wal1.py:113:8 assume
    examples/simple_wal1.py:113:8 one_of
    examples/simple_wal1.py:113:8 and
    examples/simple_wal1.py:115:12 assign
    examples/simple_wal1.py:116:12 assign
    [State 8]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: [Delete(1), Delete(1), Delete(1)]
      log_synced: 3
      pc: PC.Sync
      pending: Delete(1)
  [Step 9]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:117:4 alt
    examples/simple_wal1.py:117:4 and
    examples/simple_wal1.py:118:8 assume
    examples/simple_wal1.py:120:8 assign
    examples/simple_wal1.py:121:8 assign
    examples/simple_wal1.py:122:8 assign
    examples/simple_wal1.py:123:8 assign
    examples/simple_wal1.py:124:8 assign
    [State 9]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: [Delete(1), Delete(1), Delete(1), Delete(1)]
      log_synced: 4
      pc: PC.Wait
      pending: Nop
  [Step 10]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:117:4 alt
    examples/simple_wal1.py:103:4 and
    examples/simple_wal1.py:104:8 assume
    examples/simple_wal1.py:105:8 assume
    examples/simple_wal1.py:106:8 and
    examples/simple_wal1.py:106:8 assume
    examples/simple_wal1.py:106:8 one_of
    examples/simple_wal1.py:106:8 and
    examples/simple_wal1.py:106:8 assume
    examples/simple_wal1.py:106:8 one_of
    examples/simple_wal1.py:106:8 and
    examples/simple_wal1.py:108:12 assign
    examples/simple_wal1.py:109:12 assign
    [State 10]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: [Delete(1), Delete(1), Delete(1), Delete(1)]
      log_synced: 4
      pc: PC.Sync
      pending: Set((1, 4))
  [Step 11]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:117:4 alt
    examples/simple_wal1.py:117:4 and
    examples/simple_wal1.py:118:8 assume
    examples/simple_wal1.py:120:8 assign
    examples/simple_wal1.py:121:8 assign
    examples/simple_wal1.py:122:8 assign
    examples/simple_wal1.py:123:8 assign
    examples/simple_wal1.py:124:8 assign
    [State 11]
      kv_mem: Map(1 -> 4, 2 -> 0)
      log: [Delete(1), Delete(1), Delete(1), Delete(1), Set((1, 4))]
      log_synced: 5
      pc: PC.Wait
      pending: Nop
  [Step 12]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:132:4 and
    examples/simple_wal1.py:136:8 assign
    examples/simple_wal1.py:137:8 assign
    examples/simple_wal1.py:138:8 assign
    [State 12]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: [Delete(1), Delete(1), Delete(1), Delete(1), Set((1, 4))]
      log_synced: 5
      pc: PC.Start
      pending: Nop
  [Step 13]
    examples/simple_wal1.py:132:4 alt
    examples/simple_wal1.py:117:4 alt
    examples/simple_wal1.py:97:4 and
    examples/simple_wal1.py:98:8 assume
    examples/simple_wal1.py:102:8 assign
    [State 13]
      kv_mem: Map(1 -> 0, 2 -> 0)
      log: [Delete(1), Delete(1), Delete(1), Delete(1), Set((1, 4))]
      log_synced: 5
      pc: PC.Wait
      pending: Nop
Invariant violation at state 13
```

## 10. End of the story

Bob is happy with his today's achievements. Now he deserves one episode of the
Mandalorian. At some point, Bob will want to connect his WAL specification to
the real code from the [blog post][jaffray-post] by Justin Jaffray. This is a
story for another day though.


[jaffray-post]: https://justinjaffray.com/durability-and-redo-logging/
[simple_wal1.py]: ../../examples/simple_wal1.py
[simple_wal2.py]: ../../examples/simple_wal2.py
[random-simulations]: https://protocols-made-fun.com/testing/model-checking/2026/03/09/random-walks.html
