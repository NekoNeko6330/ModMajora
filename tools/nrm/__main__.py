# SPDX-FileCopyrightText: © 2025 ZeldaRET
# SPDX-License-Identifier: CC0-1.0

"""Export the changes made to the game as a Majora's Mask: Recompiled mod (.nrm).

Usage: python -m tools.nrm [options]   (or `make nrm`)

See docs/recomp.md for how it works.
"""

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import tomllib

from .base import prepare_base
from .bps import apply_patch, create_patch
from .compile import Tree, compile_tree
from .diff import ChunkIndex, load_objects, load_vanilla_symbols, select
from .layout import ROM_SYMBOL_RE, build_rom, read_symbol_sections, read_symbols
from .modwriter import write_mod_object
from .objfile import Chunk
from .package import link_mod, run_mod_tool, write_data_syms
from .spec import parse_spec
from .toolchain import get_n64recomp_tools

ROOT = Path(__file__).resolve().parents[2]
NRM_DIR = Path(__file__).resolve().parent

# The vanilla dmadata segment holds 0x6200 bytes: 1568 entries, including the end marker
DMADATA_CAPACITY = 1568

DEFAULT_MANIFEST = {
    "game_id": "mm",
    "minimum_recomp_version": "1.2.0",
}


def log(msg: str):
    print(f"[nrm] {msg}", flush=True)


def parse_feature_args(values: list[str]) -> dict[str, str]:
    features = {}
    for v in values:
        name, _, value = v.partition("=")
        if not name.startswith("FEATURE_"):
            name = "FEATURE_" + name
        features[name] = value or "1"
    return features


def load_config(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"Config file {path} not found (see docs/recomp.md)")
    return tomllib.loads(path.read_text())


def recomp_sources(root: Path) -> list[Path]:
    src_dir = root / "recomp" / "src"
    if not src_dir.exists():
        return []
    return sorted(src_dir.rglob("*.c"))


def main():
    ap = argparse.ArgumentParser(prog="python -m tools.nrm", description=__doc__.split("\n")[0])
    ap.add_argument("--config", type=Path, default=ROOT / "recomp" / "nrm.toml")
    ap.add_argument("-v", "--version", default="n64-us")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument(
        "-F",
        "--feature",
        action="append",
        default=[],
        metavar="FEATURE_X=value",
        help="Override a feature for the recomp build (also settable in the config's [features] table)",
    )
    ap.add_argument("--skip-make", action="store_true", help="Don't run make for the current tree")
    ap.add_argument("--allow-force-patch", action="store_true", default=None)
    args = ap.parse_args()

    t0 = time.time()
    os.chdir(ROOT)
    config = load_config(args.config)
    version = args.version
    if version != "n64-us":
        raise SystemExit("Only n64-us is supported by Majora's Mask: Recompiled")
    jobs = args.jobs
    allow_force_patch = (
        args.allow_force_patch if args.allow_force_patch is not None else config.get("allow_force_patch", False)
    )

    features = {k if k.startswith("FEATURE_") else "FEATURE_" + k: str(int(v) if isinstance(v, bool) else v)
                for k, v in config.get("features", {}).items()}
    features.update(parse_feature_args(args.feature))
    feature_defines = [f"-D{k}_OVERRIDE={v}" for k, v in sorted(features.items())]

    manifest = dict(DEFAULT_MANIFEST)
    manifest.update(config.get("manifest", {}))
    for key in ("id", "version", "display_name", "authors"):
        if key not in manifest:
            raise SystemExit(f"[manifest] {key} is missing from {args.config}")
    mod_filename = config.get("mod_filename", manifest["id"])

    out_dir = ROOT / "build" / "recomp" / "mod"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Tools and vanilla base
    n64recomp, modtool = get_n64recomp_tools(ROOT)
    base_ref = config.get("base_commit")
    if not base_ref:
        raise SystemExit(f"base_commit is missing from {args.config}")
    base = prepare_base(ROOT, base_ref, version, n64recomp, NRM_DIR / f"overlays.{version}.txt", jobs)
    log(f"Vanilla base: {base.commit}")

    # 2. Build the current tree for the recomp target
    build_dir = f"build/{version}-recomp"
    if not args.skip_make:
        make = [
            "make",
            f"VERSION={version}",
            "TARGET=recomp",
            "COMPILER=gcc",
            "NON_MATCHING=1",
            "COMPARE=0",
            f"-j{jobs}",
            *[f"{k}={v}" for k, v in sorted(features.items())],
            "rom",
        ]
        log(" ".join(make))
        subprocess.run(make, cwd=ROOT, check=True)
    cur_elf = ROOT / build_dir / f"mm-{version}.elf"
    cur_rom = ROOT / build_dir / f"mm-{version}.z64"
    cur_segments = parse_spec(ROOT / build_dir / "spec", build_dir)
    cur_syms = read_symbols(cur_elf)
    cur_symbol_sections = read_symbol_sections(cur_elf)

    # 3. ROM layout (data files)
    vanilla_syms = read_symbols(base.elf)
    vanilla_code_segments = set(re.findall(r'^name = "\.\.([^"]+)"', base.func_syms.read_text(), re.M))
    vanilla_rom = (ROOT / "baseroms" / version / "baserom.z64").read_bytes()
    log("Building the ROM layout")
    layout = build_rom(
        vanilla_rom,
        (base.root / "build" / f"{version}-ido" / f"mm-{version}.z64").read_bytes(),
        vanilla_syms,
        vanilla_code_segments,
        DMADATA_CAPACITY,
        cur_rom.read_bytes(),
        cur_elf,
        cur_syms,
        cur_symbol_sections,
        cur_segments,
    )
    code_segments = vanilla_code_segments | set(layout.new_code_segments)

    # 4. Compile the code of both trees with the mod flags
    def code_objects(segments):
        return [o for s in segments if s.name in code_segments for o in s.includes]

    cur_tree = Tree(ROOT, build_dir, f"extracted/{version}", version)
    extra_sources = recomp_sources(ROOT)
    cur_obj_list = code_objects(cur_segments) + [
        f"{build_dir}/{p.relative_to(ROOT).with_suffix('.o')}" for p in extra_sources
    ]
    base_segments = parse_spec(base.root / base.tree.build_dir / "spec", base.tree.build_dir)
    log(f"Compiling ({len(cur_obj_list)} objects)")
    cur_objs = compile_tree(cur_tree, cur_obj_list, ROOT / "build" / "recomp" / "obj", feature_defines, jobs)
    base_objs = compile_tree(
        base.tree, code_objects(base_segments), base.root.parent / "obj", [], jobs
    )
    cur_index = ChunkIndex(load_objects(cur_objs, build_dir))
    base_index = ChunkIndex(load_objects(base_objs, base.tree.build_dir))
    vs = load_vanilla_symbols(base.func_syms, base.data_syms, base.map, base.tree.build_dir)

    # 5. Symbols whose value in the recomp game differs from vanilla
    referenced = set()
    for chunk in cur_index.by_key.values():
        for r in chunk.relocs:
            if r.target[0] == "g" and r.target not in cur_index:
                referenced.add(r.target[1])

    overrides: dict[str, int] = dict(layout.rom_symbols)
    seg_sym_re = re.compile(r"^_(.+?)Segment[A-Z][A-Za-z]*$")
    new_code = set(layout.new_code_segments)
    for name in referenced:
        if name in overrides:
            continue
        value = cur_syms.get(name)
        if value is None:
            continue
        m = seg_sym_re.match(name)
        if m is not None and m.group(1) in new_code:
            # Internal actor: no overlay (see ActorOverlay)
            overrides[name] = 0
            continue
        section = cur_symbol_sections.get(name, "")
        seg = section[2:].removesuffix(".bss") if section.startswith("..") else None
        if seg is None and m is not None:
            seg = m.group(1)
        if seg is None or seg in code_segments or f"_{seg}SegmentRomStart" not in cur_syms:
            continue
        if cur_syms[f"_{seg}SegmentRomStart"] == cur_syms.get(f"_{seg}SegmentRomEnd"):
            # No ROM content (e.g. buffers in RAM): its addresses only depend on the build's memory layout
            continue
        if value >= 0x80000000:
            # Data at a fixed RAM address: the recomp game keeps the vanilla RAM layout
            continue
        if ROM_SYMBOL_RE.match(name):
            continue
        if vanilla_syms.get(name) != value:
            overrides[name] = value
    changed_values = set(overrides)

    # dmadata grew past its capacity: the table in RAM moves to the mod
    if layout.dmadata_relocated_entries is not None:
        key = ("g", "gDmaDataTable")
        cur_index.by_key[key] = Chunk(
            key=key,
            tu="src/dmadata/dmadata",
            name="gDmaDataTable",
            is_func=False,
            is_global=True,
            section=".bss.gDmaDataTable",
            kind="bss",
            data=b"",
            size=16 * layout.dmadata_relocated_entries,
            align=16,
            relocs=[],
        )

    # 6. Select what goes in the mod
    forced_new_tus = {p.relative_to(ROOT).with_suffix("").as_posix() for p in extra_sources}
    for seg in cur_segments:
        if seg.name in new_code:
            forced_new_tus.update(o[len(build_dir) + 1 : -len(".o")] for o in seg.includes)
    selection = select(cur_index, base_index, vs, changed_values, forced_new_tus)

    # 7. Conflicts with the base recomp patches
    recomp_patched = {
        line.strip()
        for line in (NRM_DIR / "recomp_patched_functions.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    conflicts = {k for k in selection.patches if selection.names[k] in recomp_patched}
    if conflicts and not allow_force_patch:
        lines = [f"  {selection.names[k]}  ({selection.reasons[k]})" for k in sorted(conflicts, key=str)]
        raise SystemExit(
            "These functions are already patched by Majora's Mask: Recompiled:\n"
            + "\n".join(lines)
            + "\nReplacing them disables the recomp's own changes to them (e.g. widescreen, camera, "
            "input). Wrap your changes in `#if TARGET_N64` / a FEATURE (see include/modmajora_config.h),"
            " or set allow_force_patch = true in recomp/nrm.toml to replace them anyway."
        )

    # 8. Every reference to the vanilla game must be resolvable
    known = vs.all_names | set(overrides)
    unresolved = sorted({n for n in selection.externals.values() if n not in known})
    if unresolved:
        raise SystemExit(
            "The mod references symbols that don't exist in the vanilla game:\n  "
            + "\n  ".join(unresolved)
            + "\n(code segments added to the game must be actors overlays, data files can't reference new code)"
        )

    # 9. Build the mod
    for f in out_dir.glob("*"):
        if f.is_file():
            f.unlink()
    if not selection.chunks:
        # RecompModTool needs at least one section (e.g. a mod that only changes data files)
        key = ("g", "__modmajora_placeholder")
        selection.chunks[key] = Chunk(key, "", key[1], False, True, ".rodata", "rodata", bytes(4), 4, 4, [])
        selection.names[key] = key[1]
        selection.reasons[key] = "placeholder"
    mod_o = out_dir / "mod.o"
    write_mod_object(selection, mod_o, conflicts)
    elf = link_mod(mod_o, out_dir)
    data_syms = out_dir / "datasyms.toml"
    write_data_syms(base.data_syms, data_syms, overrides)

    additional = []
    rom_changed = layout.rom != vanilla_rom
    if rom_changed:
        patch = create_patch(vanilla_rom, layout.rom)
        assert apply_patch(vanilla_rom, patch) == layout.rom
        (out_dir / "patch.bps").write_bytes(patch)
        additional.append(out_dir / "patch.bps")
    thumb = config.get("thumbnail")
    if thumb:
        thumb_path = ROOT / thumb
        shutil.copy2(thumb_path, out_dir / "thumb.png")
        additional.append(out_dir / "thumb.png")

    nrm = run_mod_tool(
        modtool,
        out_dir,
        manifest,
        mod_filename,
        elf,
        base.func_syms,
        [data_syms, base.data_syms.parent / "datasyms_static.toml"],
        additional,
    )
    final = ROOT / "build" / f"{mod_filename}.nrm"
    shutil.copy2(nrm, final)

    # 10. Report
    report = [f"Mod: {final.relative_to(ROOT)} ({final.stat().st_size} bytes)"]
    n_patch = len(selection.patches)
    n_new_funcs = sum(1 for k, c in selection.chunks.items() if c.is_func and k not in selection.patches)
    n_data = sum(1 for c in selection.chunks.values() if not c.is_func)
    report.append(f"  {n_patch} patched functions ({len(conflicts)} forced), {n_new_funcs} new functions, {n_data} data")
    if rom_changed:
        report.append(
            f"  ROM patch: {len(layout.changed_segments)} changed files, {len(layout.new_segments)} new files,"
            f" patch.bps {(out_dir / 'patch.bps').stat().st_size} bytes"
        )
    if layout.new_code_segments:
        report.append(f"  New code (internal actors): {', '.join(layout.new_code_segments)}")
    report += ["  " + r for r in layout.report]
    report += ["  warning: " + w for w in selection.warnings]
    lines = [f"{'PATCH' if k in selection.patches else '     '} {selection.names[k]}: {selection.reasons[k]}"
             for k in sorted(selection.chunks, key=lambda k: selection.names[k])]
    (out_dir / "report.txt").write_text("\n".join(report + [""] + lines) + "\n")
    for line in report:
        log(line)
    log(f"Details in {(out_dir / 'report.txt').relative_to(ROOT)} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
