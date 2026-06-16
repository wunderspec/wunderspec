#!/usr/bin/env python3
"""Run wunderspec examples from examples/examples.yaml.

For each file listed in the YAML config, runs ``wunderspec run`` once per @instance
factory, checking every ``@invariant``-decorated definition and finding every
``@example``-decorated definition. Init and step action names are discovered
from the source via AST inspection.

YAML fields:
  file       – filename relative to examples/, e.g. ``readers_writers.py``
  instances  – optional space-separated list of ``@instance`` names to run;
               if empty, the spec has no @instance factories
  invariants – optional space-separated list of @invariant names to run;
               if omitted, all invariants in the spec are run; if explicitly
               empty, no invariants are run
  examples   – optional space-separated list of @example names to run;
               if omitted, all examples in the spec are run; if explicitly
               empty, no examples are run
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts._spec_utils import (  # noqa: E402
    find_example_names,
    find_init_action_name,
    find_invariant_names,
    find_step_action_name,
    load_examples_config,
)

ROOT = Path(__file__).parent.parent  # scripts/ -> project root
EXAMPLES_DIR = ROOT / "examples"
CONFIG_PATH = EXAMPLES_DIR / "examples.yaml"


def main() -> int:
    failed = False

    for row in load_examples_config(CONFIG_PATH):
        spec_file = str(row["file"]).strip()
        if not spec_file:
            continue

        # Read explicit instance and invariant lists from YAML.
        # Missing invariant/example fields mean auto-discovery/default behavior.
        yaml_instances = list(row["instances"])
        yaml_invariants = list(row["invariants"])
        yaml_examples = list(row["examples"])
        invariants_auto = bool(row["invariants_auto"])
        examples_auto = bool(row["examples_auto"])
        example_run_seeds = dict(row["example_run_seeds"])
        example_run_max_samples = dict(row["example_run_max_samples"])
        timeout = row["timeout"]
        timeout_flags = ["--timeout", str(timeout)] if timeout is not None else []

        spec_path = EXAMPLES_DIR / spec_file
        init = find_init_action_name(spec_path)
        step = find_step_action_name(spec_path, init)
        invariants = find_invariant_names(spec_path)
        examples = find_example_names(spec_path)
        if not invariants_auto:
            invariants = set(yaml_invariants)
        if not examples_auto:
            examples = set(yaml_examples)

        # Build list of instance flag groups: one per listed instance, or [None].
        if yaml_instances:
            instance_flag_groups: list[list[str] | None] = [
                ["--instance", name] for name in yaml_instances
            ]
        else:
            instance_flag_groups = [None]

        # Add examples/ to PYTHONPATH so spec-local imports resolve.
        env = {**os.environ, "PYTHONPATH": str(EXAMPLES_DIR)}

        for instance_flags in instance_flag_groups:
            for name in sorted(invariants):
                cmd = [
                    "wunderspec",
                    "run",
                    "--init",
                    init,
                    "--step",
                    step,
                    *(instance_flags or []),
                    "--max-samples",
                    "100",
                    "--max-steps",
                    "20",
                    *timeout_flags,
                    "--property",
                    name,
                    f"examples/{spec_file}",
                ]
                instance_label = (
                    instance_flags[1] if instance_flags else "(no instance)"
                )
                print(
                    f"=== {spec_file}: checking invariant '{name}'"
                    f" with instance '{instance_label}' ===",
                    flush=True,
                )
                result = subprocess.run(cmd, env=env, cwd=ROOT)
                if result.returncode != 0:
                    print(
                        f"FAILED: {spec_file} invariant '{name}'"
                        f" with instance '{instance_label}'",
                        flush=True,
                    )
                    failed = True
            for name in sorted(examples):
                max_samples = str(example_run_max_samples.get(name, 100))
                cmd = [
                    "wunderspec",
                    "run",
                    "--init",
                    init,
                    "--step",
                    step,
                    *(instance_flags or []),
                    "--max-samples",
                    max_samples,
                    "--max-steps",
                    "20",
                    *timeout_flags,
                    "--property",
                    name,
                    f"examples/{spec_file}",
                ]
                if name in example_run_seeds:
                    cmd[cmd.index("--max-steps") : cmd.index("--max-steps")] = [
                        "--seed",
                        str(example_run_seeds[name]),
                    ]
                instance_label = (
                    instance_flags[1] if instance_flags else "(no instance)"
                )
                print(
                    f"=== {spec_file}: finding example '{name}'"
                    f" with instance '{instance_label}'"
                    f" (max-samples={max_samples}"
                    f"{', seed=' + str(example_run_seeds[name]) if name in example_run_seeds else ''}) ===",
                    flush=True,
                )
                result = subprocess.run(cmd, env=env, cwd=ROOT)
                if result.returncode != 2:
                    print(
                        f"FAILED: {spec_file} example '{name}'"
                        f" with instance '{instance_label}'",
                        flush=True,
                    )
                    failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
