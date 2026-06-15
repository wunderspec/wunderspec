#!/usr/bin/env python3
#
# A random walk test.
#
# Igor Konnov, 2026

import sys

from examples.readers_writers import ReadersWritersState, init, safety, step
from wunderspec import *
from wunderspec.random_walk import WalkSettings, random_traces, random_traces_debug


def print_trace(trace: tuple[StateView, ...]):
    for i, s in enumerate(trace):
        print(f"State {i}:")
        for k, v in s.to_python()._asdict().items():  # type: ignore
            print(f"  {k}: {v}")


if __name__ == "__main__":
    args = {i: a for i, a in enumerate(sys.argv[1:])}
    max_examples = int(args.get(0, "1000"))
    max_steps = int(args.get(1, "100"))
    sampler = args.get(2, "compiled")
    trace_sampler = random_traces if sampler == "compiled" else random_traces_debug

    # generate `max_examples` random walks of up to `max_steps` steps each
    proto = ReadersWritersState(NumActors=4)  # type: ignore
    settings = WalkSettings(max_steps=max_steps, max_retries_per_step=3)
    examples_count = 0
    for _seed, t in trace_sampler(proto, init, step, settings):  # type: ignore
        for i, s in enumerate(t):
            result = value(safety(s))  # type: ignore
            assert isinstance(result, BoolValue)
            if not result.value:
                print("Safety violation:", s.to_python())
                print_trace(t[: i + 1])
                examples_count = max_examples  # stop after this
                break

        examples_count += 1
        if examples_count > max_examples:
            break
