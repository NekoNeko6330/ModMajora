# SPDX-FileCopyrightText: © 2025 ZeldaRET
# SPDX-License-Identifier: CC0-1.0

"""Compile the C files of a source tree with the flags used for recomp mod code.

Both the base (vanilla) tree and the current tree are compiled with these same flags,
so that comparing the resulting objects tells which functions and data changed.
The objects of the current tree are then also used to build the mod.
"""

import concurrent.futures
import dataclasses
from pathlib import Path
import re
import shlex
import subprocess

GCC = "mips-linux-gnu-gcc"

MOD_CFLAGS = [
    "-c",
    "-G0",
    "-mips2",
    "-mabi=32",
    "-mno-abicalls",
    "-fno-pic",
    "-mno-odd-spreg",
    "-O2",
    "-ffreestanding",
    "-fno-builtin",
    "-nostdinc",
    "-funsigned-char",
    "-fno-common",
    "-fno-strict-aliasing",
    "-ffast-math",
    "-fno-unsafe-math-optimizations",
    "-fno-tree-loop-distribute-patterns",
    # Tail calls (j) to functions outside the mod are not supported by N64Recomp
    "-fno-optimize-sibling-calls",
    # Division by zero traps (teq) are not supported by N64Recomp
    "-mno-check-zero-division",
    # One section per function and data symbol, which makes extracting them easy
    "-ffunction-sections",
    "-fdata-sections",
    "-w",
]

MOD_DEFINES = [
    "-D_LANGUAGE_C",
    "-D_MIPS_SZLONG=32",
    "-DF3DEX_GBI_2",
    "-DF3DEX_GBI_PL",
    "-DGBI_DOWHILE",
    "-DNON_MATCHING",
    "-DAVOID_UB",
    "-DTARGET_RECOMP=1",
    "-DCOMPILER_GCC",
]


@dataclasses.dataclass
class Tree:
    """A source tree to compile (the base worktree or the current repo)."""

    root: Path
    # Build dir (relative to root) used for generated files (e.g. textures .inc.c, .enc.c)
    build_dir: str
    # Extracted dir (relative to root)
    extracted_dir: str
    # Version name (e.g. n64-us)
    version: str


def get_shiftjis_files(root: Path) -> set[str]:
    text = (root / "Makefile").read_text()
    m = re.search(r"^SHIFTJIS_C_FILES\s*:=\s*(.*)$", text, re.M)
    if m is None:
        return set()
    return set(m.group(1).split())


def source_for_object(tree: Tree, obj: str, shiftjis_files: set[str]) -> Path | None:
    """Find the C source file compiled to object `obj` (a path like build/n64-us/src/code/z_actor.o)"""
    prefix = tree.build_dir + "/"
    if not obj.startswith(prefix) or not obj.endswith(".o"):
        return None
    rel = obj[len(prefix) : -len(".o")]
    if rel + ".c" in shiftjis_files:
        enc = tree.root / tree.build_dir / (rel + ".enc.c")
        return enc if enc.exists() else None
    for candidate in (
        tree.root / (rel + ".c"),
        tree.root / tree.extracted_dir / (rel + ".c"),
    ):
        if candidate.exists():
            return candidate
    return None


def per_file_flags(rel: str) -> list[str]:
    flags = []
    if rel.startswith("src/"):
        flags.append("-fexec-charset=euc-jp")
    if rel.startswith("src/gcc_fix/"):
        # Uses 64-bit instructions on purpose (like libultra does)
        flags.append("-mips3")
    if rel == "src/audio/lib/seqplayer":
        flags.append("-DMML_VERSION=MML_VERSION_MM")
    if rel == "src/audio/tables/sequence_table":
        flags.append("-Iinclude/tables")
    if rel == "src/boot/build":
        flags += [
            '-DBUILD_CREATOR="modmajora"',
            '-DBUILD_DATE="recomp"',
            '-DBUILD_TIME=""',
        ]
    return flags


def compile_command(
    tree: Tree, src: Path, out: Path, rel: str, extra_defines: list[str]
) -> list[str]:
    return [
        GCC,
        *MOD_CFLAGS,
        *MOD_DEFINES,
        f"-DMM_VERSION={tree.version.upper().replace('-', '_').replace('.', '_')}",
        *extra_defines,
        "-Iinclude",
        "-Iinclude/libc",
        "-Isrc",
        f"-I{tree.build_dir}",
        "-I.",
        f"-I{tree.extracted_dir}",
        *per_file_flags(rel),
        "-MMD",
        "-MF",
        str(out.with_suffix(".d")),
        "-o",
        str(out),
        str(src),
    ]


def compile_tree(
    tree: Tree,
    objects: list[str],
    out_dir: Path,
    extra_defines: list[str],
    jobs: int,
) -> dict[str, Path]:
    """Compile the C sources of the given spec objects.

    Returns a dict mapping each spec object path to the compiled mod object.
    Objects without a C source (assembly, binary data) are skipped.
    """
    shiftjis_files = get_shiftjis_files(tree.root)
    out_dir = out_dir.resolve()

    tasks = []
    result: dict[str, Path] = {}
    for obj in objects:
        src = source_for_object(tree, obj, shiftjis_files)
        if src is None:
            continue
        rel = obj[len(tree.build_dir) + 1 : -len(".o")]
        out = out_dir / (rel + ".o")
        result[obj] = out
        tasks.append((src.resolve(), out, rel))

    def run(task):
        src, out, rel = task
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = compile_command(tree, src, out, rel, extra_defines)
        cmd_file = out.with_suffix(".cmd")
        if _is_up_to_date_abs(out, src, cmd_file, cmd, tree.root):
            return None
        proc = subprocess.run(cmd, cwd=tree.root, capture_output=True, text=True)
        if proc.returncode != 0:
            return (src, proc.stderr)
        cmd_file.write_text(shlex.join(cmd))
        return None

    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
        for err in executor.map(run, tasks):
            if err is not None:
                errors.append(err)
    if errors:
        msg = "\n".join(f"{src}:\n{stderr}" for src, stderr in errors[:10])
        raise Exception(f"Failed to compile {len(errors)} file(s) for the mod:\n{msg}")
    return result


def _is_up_to_date_abs(
    out: Path, src: Path, cmd_file: Path, cmd: list[str], root: Path
) -> bool:
    if not out.exists() or not cmd_file.exists():
        return False
    if cmd_file.read_text() != shlex.join(cmd):
        return False
    out_mtime = out.stat().st_mtime
    dep_file = out.with_suffix(".d")
    if not dep_file.exists():
        return False
    deps_text = dep_file.read_text().replace("\\\n", " ")
    deps = deps_text.split(":", 1)[1].split() if ":" in deps_text else []
    for dep in [str(src), *deps]:
        p = Path(dep)
        if not p.is_absolute():
            p = root / p
        if not p.exists() or p.stat().st_mtime > out_mtime:
            return False
    return True
