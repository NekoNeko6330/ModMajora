# SPDX-FileCopyrightText: © 2025 ZeldaRET
# SPDX-License-Identifier: CC0-1.0

"""Prepare the vanilla base: a git worktree of the base commit, built to match the vanilla ROM.

Majora's Mask: Recompiled runs the vanilla game. The base build provides:
- the vanilla ELF, from which the recomp symbol files are generated (vanilla addresses and names)
- the vanilla sources, compiled with the mod flags, to compare against the current sources
"""

import dataclasses
import os
from pathlib import Path
import shutil
import subprocess

from .compile import Tree


@dataclasses.dataclass
class Base:
    commit: str
    root: Path
    elf: Path
    map: Path
    func_syms: Path
    data_syms: Path
    tree: Tree


def _run(cmd, cwd, env=None):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True, env=env)


def resolve_commit(root: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", "--verify", ref + "^{commit}"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


SYMS_CONFIG = """\
[input]
entrypoint = 0x80080000
output_func_path = "RecompiledFuncs"
relocatable_sections_path = "overlays.txt"
elf_path = "mm-{version}.elf"
use_mdebug = true

[patches]
# Ignore sGameOverTimer, as it has incorrect mdebug info due to decomp preprocessing the object to move .data into .rodata.
ignored = [
    "sGameOverTimer"
]
"""

# Manually curated static symbols that don't show up in the ELF (from Zelda64RecompSyms)
STATIC_SYMS_EXTRA = """\
# Manually curated static symbols that don't show up in the ELF.
[[section]]
name = "..code"
rom = 0x00B3C000
vram = 0x800A5AC0
size = 0x13E4E0

symbols = [
    { name = "sGameOverTimer", vram = 0x801E1110 },
]
"""


def prepare_base(
    root: Path,
    commit_ref: str,
    version: str,
    n64recomp: Path,
    overlays_list: Path,
    jobs: int,
) -> Base:
    commit = resolve_commit(root, commit_ref)
    work = root / "build" / "recomp" / "base" / commit
    wt = work / "tree"
    ready_marker = work / ".ready"

    if not ready_marker.exists():
        if wt.exists():
            subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=root)
            shutil.rmtree(wt, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        _run(["git", "worktree", "add", "--detach", str(wt), commit], cwd=root)

        # Share the baserom and the python environment
        baserom_dir = wt / "baseroms" / version
        baserom_dir.mkdir(parents=True, exist_ok=True)
        for f in ("baserom.z64",):
            src = root / "baseroms" / version / f
            dst = baserom_dir / f
            if not dst.exists():
                os.symlink(src.resolve(), dst)
        if not (wt / ".venv").exists():
            os.symlink((root / ".venv").resolve(), wt / ".venv")

        make = ["make", f"VERSION={version}", f"-j{jobs}"]
        _run([*make, "setup"], cwd=wt)
        _run([*make, "assets"], cwd=wt)
        _run([*make, "disasm"], cwd=wt)
        # Matching build, keeping debug info for static symbols
        _run(
            [*make, "COMPILER=ido", "NON_MATCHING=0", "COMPARE=1", "KEEP_MDEBUG=1"],
            cwd=wt,
        )

        # Generate the recomp symbol files
        syms_dir = work / "syms"
        syms_dir.mkdir(parents=True, exist_ok=True)
        elf = wt / "build" / f"{version}-ido" / f"mm-{version}.elf"
        shutil.copy2(elf, syms_dir / f"mm-{version}.elf")
        shutil.copy2(overlays_list, syms_dir / "overlays.txt")
        (syms_dir / "generate_symbols.toml").write_text(SYMS_CONFIG.format(version=version))
        _run([str(n64recomp), "generate_symbols.toml", "--dump-context"], cwd=syms_dir)
        (syms_dir / f"mm-{version}.elf").unlink()
        (syms_dir / "datasyms_static.toml").write_text(STATIC_SYMS_EXTRA)

        ready_marker.write_text(commit + "\n")

    build_dir = f"build/{version}-ido"
    return Base(
        commit=commit,
        root=wt,
        elf=wt / build_dir / f"mm-{version}.elf",
        map=wt / build_dir / f"mm-{version}.map",
        func_syms=work / "syms" / "dump.toml",
        data_syms=work / "syms" / "data_dump.toml",
        tree=Tree(wt, build_dir, f"extracted/{version}", version),
    )
