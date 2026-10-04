# SPDX-FileCopyrightText: © 2025 ZeldaRET
# SPDX-License-Identifier: CC0-1.0

"""Get the N64Recomp tools (N64Recomp for symbol generation, RecompModTool for packaging mods)."""

from pathlib import Path
import shutil
import subprocess

N64RECOMP_REPO = "https://github.com/N64Recomp/N64Recomp"
N64RECOMP_COMMIT = "ffb39cdad1da5de07eaaa48bd1db4a89a7986771"


def _run(cmd, cwd=None):
    print("+", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def get_n64recomp_tools(root: Path) -> tuple[Path, Path]:
    """Return the paths to the N64Recomp and RecompModTool executables, building them if needed.

    Paths can be overridden by placing executables in tools/N64Recomp/bin/.
    """
    base = root / "tools" / "N64Recomp"
    prebuilt_dir = base / "bin"
    n64recomp = prebuilt_dir / "N64Recomp"
    modtool = prebuilt_dir / "RecompModTool"
    if n64recomp.exists() and modtool.exists():
        return n64recomp, modtool

    src = base / "src"
    if not (src / ".git").exists():
        src.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--quiet", N64RECOMP_REPO, str(src)])
    _run(["git", "-C", str(src), "fetch", "--quiet", "origin", N64RECOMP_COMMIT])
    _run(["git", "-C", str(src), "checkout", "--quiet", N64RECOMP_COMMIT])
    _run(["git", "-C", str(src), "submodule", "update", "--init", "--recursive", "--quiet"])

    build = src / "build"
    generator = ["-G", "Ninja"] if shutil.which("ninja") else []
    _run(["cmake", "-S", str(src), "-B", str(build), *generator, "-DCMAKE_BUILD_TYPE=Release"])
    _run(["cmake", "--build", str(build), "--target", "N64Recomp", "RecompModTool", "--parallel"])

    prebuilt_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(build / "N64Recomp", n64recomp)
    shutil.copy2(build / "RecompModTool", modtool)
    return n64recomp, modtool
