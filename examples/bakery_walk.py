#!/usr/bin/env python3
#
# A random walk test for the Bakery mutual exclusion algorithm.
#
# Igor Konnov, 2026

import sys

from examples.bakery import BakeryState, Init, Next
from wunderspec.random_walk import WalkSettings, random_traces, random_traces_debug

if __name__ == "__main__":
    args = {i: a for i, a in enumerate(sys.argv[1:])}
    max_examples = int(args.get(0, "1000"))
    max_steps = int(args.get(1, "100"))
    sampler = args.get(2, "compiled")
    bound = int(args.get(3, str(2**31 - 1)))
    trace_sampler = random_traces if sampler == "compiled" else random_traces_debug

    # generate `max_examples` random walks of up to `max_steps` steps each
    proto = BakeryState(N=3)  # type: ignore
    settings = WalkSettings(max_steps=max_steps, max_retries_per_step=3, bound=bound)
    examples_count = 0
    for _seed, t in trace_sampler(proto, Init, Next, settings):  # type: ignore
        examples_count += 1
        if examples_count > max_examples:
            break
