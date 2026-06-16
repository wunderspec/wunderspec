#!/usr/bin/env python3
"""Convert examples from examples/examples.yaml to TLA+ and validate with SANY.

For each file listed in the YAML config, this script:
1. Converts the base spec to ``<build_dir>/<stem>.tla``
2. Converts each listed ``@instance`` to ``<build_dir>/MC_<instance>_<stem>.tla``
3. Downloads Apalache support modules into ``build_dir`` if they are missing
4. Runs SANY on every generated ``.tla`` file with ``build_dir`` on the classpath
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts._spec_utils import load_examples_config  # noqa: E402

ROOT = Path(__file__).parent.parent
EXAMPLES_DIR = ROOT / "examples"
CONFIG_PATH = EXAMPLES_DIR / "examples.yaml"
BUILD_DIR = ROOT / ".build"
TLA2TOOLS_JAR = ROOT / "tla2tools.jar"
SUPPORT_MODULES = {
    "Variants.tla": "https://raw.githubusercontent.com/apalache-mc/apalache/main/src/tla/Variants.tla",
    "Apalache.tla": "https://raw.githubusercontent.com/apalache-mc/apalache/main/src/tla/Apalache.tla",
}


def clear_build_dir(build_dir: Path) -> None:
    """Remove the generated build directory if it matches the expected location."""
    resolved_root = ROOT.resolve()
    resolved_build_dir = build_dir.resolve(strict=False)
    expected_build_dir = (resolved_root / ".build").resolve(strict=False)

    if resolved_build_dir != expected_build_dir:
        raise SystemExit(
            f"Refusing to remove unexpected build directory: {resolved_build_dir}"
        )
    if resolved_build_dir.parent != resolved_root:
        raise SystemExit(
            f"Refusing to remove build directory outside project root: {resolved_build_dir}"
        )
    if resolved_build_dir.name != ".build":
        raise SystemExit(
            f"Refusing to remove directory with unexpected name: {resolved_build_dir}"
        )

    if build_dir.exists():
        shutil.rmtree(build_dir)


def ensure_support_modules(build_dir: Path) -> None:
    build_dir.mkdir(parents=True, exist_ok=True)
    for filename, url in SUPPORT_MODULES.items():
        dst = build_dir / filename
        if dst.exists():
            continue
        src = ROOT / filename
        if src.exists():
            shutil.copy(src, dst)
            continue
        print(f"Downloading {filename}...", flush=True)
        urllib.request.urlretrieve(url, dst)


def run_cmd(cmd: list[str], *, env: dict[str, str], cwd: Path = ROOT) -> None:
    result = subprocess.run(cmd, cwd=cwd, env=env)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> int:
    if not TLA2TOOLS_JAR.exists():
        print("Error: tla2tools.jar not found", file=sys.stderr)
        return 1

    clear_build_dir(BUILD_DIR)
    ensure_support_modules(BUILD_DIR)

    env = {**os.environ, "PYTHONPATH": str(EXAMPLES_DIR)}
    generated: list[Path] = []

    for row in load_examples_config(CONFIG_PATH):
        spec_file = str(row["file"]).strip()
        if not spec_file:
            continue

        stem = Path(spec_file).stem
        instances = cast(list[str], row["instances"])
        base_out = BUILD_DIR / f"{stem}.tla"
        print(f"=== Converting {spec_file} ===", flush=True)
        run_cmd(
            [
                sys.executable,
                "-m",
                "wunderspec.cli",
                "convert",
                "--from",
                f"examples/{spec_file}",
                "--to",
                str(base_out),
            ],
            env=env,
        )
        generated.append(base_out)

        for instance in instances:
            wrapper_out = BUILD_DIR / f"MC_{instance}_{stem}.tla"
            print(
                f"=== Converting {spec_file} instance {instance} ===",
                flush=True,
            )
            run_cmd(
                [
                    sys.executable,
                    "-m",
                    "wunderspec.cli",
                    "convert",
                    "--from",
                    f"examples/{spec_file}",
                    "--to",
                    str(wrapper_out),
                    "--instance",
                    str(instance),
                ],
                env=env,
            )
            generated.append(wrapper_out)

    classpath = f"{TLA2TOOLS_JAR}{os.pathsep}{BUILD_DIR}"
    for tla_file in generated:
        print(f"=== Checking {tla_file.relative_to(ROOT)} ===", flush=True)
        run_cmd(
            ["java", "-cp", classpath, "tla2sany.SANY", tla_file.name],
            env=env,
            cwd=BUILD_DIR,
        )

    print("All examples and listed instances converted and passed SANY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
