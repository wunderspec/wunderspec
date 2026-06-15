#!/usr/bin/env python
# A random walk test.

import sys

from simple_ponzi_machine import *

from examples.simple_ponzi_machine import PonziMachineState
from wunderspec.random_walk import WalkSettings, random_traces, random_traces_debug

if __name__ == "__main__":
    args = {i: a for i, a in enumerate(sys.argv[1:])}
    max_examples = int(args.get(0, "1000"))
    max_steps = int(args.get(1, "100"))
    sampler = args.get(2, "compiled")
    trace_sampler = random_traces if sampler == "compiled" else random_traces_debug

    # generate `max_examples` random walks of up to `max_steps` steps each
    settings = WalkSettings(max_steps=max_steps, max_retries_per_step=3)
    examples_count = 0
    for _seed, t in trace_sampler(PonziMachineState(), init, step, settings):
        examples_count += 1
        if examples_count > max_examples:
            break
