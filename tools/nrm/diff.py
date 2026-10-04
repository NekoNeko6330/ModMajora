# SPDX-FileCopyrightText: © 2025 ZeldaRET
# SPDX-License-Identifier: CC0-1.0

"""Compare the base (vanilla) and current objects and select what goes in the mod.

Rules:
- A function that changed, or is new, goes in the mod. Changed vanilla functions become patches.
- A data symbol that changed, or is new, goes in the mod (as a copy).
  Everything referencing a changed data symbol must then use the mod's copy, so all functions and
  data referencing it are added too (recursively for data).
- A function referencing a symbol whose value changed (e.g. an asset that moved, or a file that moved
  in ROM) goes in the mod.
- Anything referenced from the mod that doesn't exist in the vanilla game (string literals, compiler
  generated helpers, function-local statics, ...) is copied into the mod.
- Everything else is referenced from the vanilla game by name, using the recomp symbol files.
"""

import bisect
import dataclasses
from pathlib import Path
import re
import tomllib

from .objfile import R_MIPS_32, Chunk, Key, ObjectFile, anon_cmp_key


@dataclasses.dataclass
class VanillaSymbols:
    """Names and addresses of the vanilla symbols, from the recomp symbol files and the base map file."""

    func_names: set[str]
    data_names: set[str]
    # name -> vram
    vram_by_name: dict[str, int]
    # base symbol name (without renaming suffix) -> list of (vram, recomp symbol name)
    by_base_name: dict[str, list[tuple[int, str]]]
    # tu -> sorted list of (start, end) vram ranges
    tu_ranges: dict[str, list[tuple[int, int]]]
    # recomp section name -> functions names
    section_by_func: dict[str, str]

    @property
    def all_names(self):
        return self.func_names | self.data_names


def load_vanilla_symbols(func_syms: Path, data_syms: Path, base_map: Path, base_build_dir: str):
    func_names: set[str] = set()
    data_names: set[str] = set()
    vram_by_name: dict[str, int] = {}
    section_by_func: dict[str, str] = {}
    by_base_name: dict[str, list[tuple[int, str]]] = {}

    def add(name: str, vram: int):
        vram_by_name.setdefault(name, vram)
        by_base_name.setdefault(name, []).append((vram, name))

    funcs = tomllib.loads(func_syms.read_text())
    for section in funcs.get("section", []):
        for f in section.get("functions", []):
            func_names.add(f["name"])
            section_by_func[f["name"]] = section["name"]
            add(f["name"], f["vram"])
    datas = tomllib.loads(data_syms.read_text())
    for section in datas.get("section", []):
        for d in section.get("symbols", []):
            data_names.add(d["name"])
            add(d["name"], d["vram"])

    tu_ranges: dict[str, list[tuple[int, int]]] = {}
    line_re = re.compile(
        r"^ (\.text|\.data|\.rodata|\.bss|\.rodata\.\S+)\s+0x([0-9a-f]+)\s+0x([0-9a-f]+)\s+(\S+\.o)$"
    )
    prefix = base_build_dir + "/"
    for line in base_map.read_text().splitlines():
        m = line_re.match(line)
        if m is None:
            continue
        start = int(m.group(2), 16)
        size = int(m.group(3), 16)
        obj = m.group(4)
        if size == 0 or not obj.startswith(prefix):
            continue
        tu = obj[len(prefix) : -len(".o")]
        tu_ranges.setdefault(tu, []).append((start, start + size))
    for ranges in tu_ranges.values():
        ranges.sort()

    return VanillaSymbols(
        func_names=func_names,
        data_names=data_names,
        vram_by_name=vram_by_name,
        by_base_name=by_base_name,
        tu_ranges=tu_ranges,
        section_by_func=section_by_func,
    )


class ChunkIndex:
    def __init__(self, objects: dict[str, ObjectFile]):
        self.by_key: dict[Key, Chunk] = {}
        self.duplicates: list[Key] = []
        for obj in objects.values():
            for chunk in obj.chunks:
                if chunk.key in self.by_key:
                    self.duplicates.append(chunk.key)
                    if not (chunk.kind.startswith(".") and not self.by_key[chunk.key].kind.startswith(".")):
                        continue
                    # Explicit mod code (e.g. RECOMP_PATCH in recomp/src) replaces the tree's version
                self.by_key[chunk.key] = chunk

    def __contains__(self, key):
        return key in self.by_key

    def get(self, key):
        return self.by_key.get(key)

    def signature(self, chunk: Chunk) -> tuple:
        relocs = []
        for r in chunk.relocs:
            target = self.by_key.get(r.target)
            if target is not None and target.is_anon:
                cmp_target, cmp_addend = anon_cmp_key(target, r.addend)
            else:
                cmp_target, cmp_addend = r.target, r.addend
            relocs.append((r.offset, r.type, cmp_target, cmp_addend))
        return (
            chunk.kind,
            chunk.is_func,
            chunk.size,
            chunk.normalized_data(),
            tuple(relocs),
        )


@dataclasses.dataclass
class Selection:
    # Chunks to put in the mod, with the name to give them
    chunks: dict[Key, Chunk]
    # Keys of chunks that are patches of vanilla functions
    patches: set[Key]
    # Name of each chunk in the mod (for patches: the vanilla recomp symbol name)
    names: dict[Key, str]
    # References from the mod to vanilla symbols: key -> recomp symbol name
    externals: dict[Key, str]
    # Why each chunk was selected (for reporting)
    reasons: dict[Key, str]
    warnings: list[str]
    # Changed vanilla data patched in place (in the ROM), with the same size and layout:
    # key -> (vanilla name, relocations as (offset, target recomp symbol name, addend), size)
    # The size can be smaller than the chunk: data that grew is copied into the mod, and its original
    # part is also updated in place for the protected functions that keep using the vanilla data.
    in_place: dict[Key, tuple[str, list[tuple[int, str, int]], int]] = dataclasses.field(default_factory=dict)


def _static_vanilla_name(vs: VanillaSymbols, tu: str, name: str) -> str | None:
    m = re.match(r"^([A-Za-z_]\w*)\.\d+$", name)
    if m is not None:
        # gcc function-local static (name.N): matched with the vanilla static of the same name, if unique
        name = m.group(1)
    elif "." in name:
        # gcc-generated names (clones like .constprop.0): no vanilla counterpart
        return None
    ranges = vs.tu_ranges.get(tu)
    if not ranges:
        return None
    candidates = []
    for vram, sym_name in _candidates_for(vs, name):
        for start, end in ranges:
            if start <= vram < end:
                candidates.append(sym_name)
                break
    if len(candidates) == 1:
        return candidates[0]
    return None


def _candidates_for(vs: VanillaSymbols, name: str):
    # Exact name, and names renamed by N64Recomp: name_section or name_section_ADDRESS
    if name in vs.vram_by_name:
        yield vs.vram_by_name[name], name
    prefix = name + "_"
    for sym_name in _names_with_prefix(vs, prefix):
        yield vs.vram_by_name[sym_name], sym_name


def _names_with_prefix(vs: VanillaSymbols, prefix: str):
    sorted_names = getattr(vs, "_sorted_names", None)
    if sorted_names is None:
        sorted_names = sorted(vs.vram_by_name)
        vs._sorted_names = sorted_names
    i = bisect.bisect_left(sorted_names, prefix)
    while i < len(sorted_names) and sorted_names[i].startswith(prefix):
        yield sorted_names[i]
        i += 1


def _sanitize(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", s)


def select(
    current: ChunkIndex,
    base: ChunkIndex,
    vs: VanillaSymbols,
    changed_values: set[str],
    forced_new_tus: set[str] = frozenset(),
    sections: list[tuple[int, int, bool]] = (),
    protected: set[str] = frozenset(),
) -> Selection:
    """Select the chunks that go in the mod.

    changed_values: names of external symbols (assets, ROM addresses, ...) whose value in the recomp
    version of the game differs from vanilla.
    forced_new_tus: translation units whose content is entirely new (e.g. new actors), all their chunks
    are put in the mod.
    sections: vanilla code sections as (vram start, vram end, relocatable), used to check that data
    patched in place in overlays keeps the same relocations.

    Changed vanilla data that keeps its size, and only points to vanilla addresses, is patched in place
    (in the ROM: recomp loads code segments data from the ROM). Other changed data is copied into the
    mod, and everything using it is patched to use the copy.
    protected: names of vanilla functions that should not be patched only because they use data copied
    into the mod (functions already patched by the recomp). They keep using the vanilla data, whose
    original part is updated in place, if possible.
    """
    warnings: list[str] = []
    reasons: dict[Key, str] = {}

    vanilla_name_cache: dict[Key, str | None] = {}

    def vanilla_name(key: Key) -> str | None:
        """Name of the vanilla counterpart of a chunk/symbol, if any"""
        if key in vanilla_name_cache:
            return vanilla_name_cache[key]
        name = None
        if key[0] == "g":
            if key[1] in vs.vram_by_name:
                name = key[1]
        elif key[0] == "s":
            name = _static_vanilla_name(vs, key[1], key[2])
        vanilla_name_cache[key] = name
        return name

    # 1. Changed and new chunks
    selected: dict[Key, Chunk] = {}
    for key, chunk in current.by_key.items():
        if chunk.is_anon:
            continue
        if chunk.kind.startswith("."):
            # Special mod sections (hooks, callbacks, exports, ...)
            selected[key] = chunk
            reasons[key] = "mod-only"
            continue
        if chunk.tu.startswith("src/gcc_fix/"):
            # Compiler support functions: only included if used
            continue
        if chunk.tu in forced_new_tus:
            selected[key] = chunk
            reasons[key] = "new file"
            continue
        b = base.get(key)
        if b is None:
            selected[key] = chunk
            reasons[key] = "new"
        elif current.signature(chunk) != base.signature(b):
            selected[key] = chunk
            reasons[key] = "changed"

    # 2. Chunks referencing symbols whose value changed
    if changed_values:
        for key, chunk in current.by_key.items():
            if key in selected or chunk.is_anon:
                continue
            for r in chunk.relocs:
                if r.target[0] == "g" and r.target not in current and r.target[1] in changed_values:
                    selected[key] = chunk
                    reasons[key] = f"references {r.target[1]} (moved)"
                    break

    # 3. Changed data patched in place, and closure: everything referencing changed/new data with a
    #    vanilla counterpart that is not patched in place must use the mod's copy.
    users: dict[Key, set[Key]] = {}
    for key, chunk in current.by_key.items():
        for r in chunk.relocs:
            users.setdefault(r.target, set()).add(key)

    def section_of(vram: int | None):
        if vram is None:
            return None
        for start, end, relocatable in sections:
            if start <= vram < end:
                return (start, end, relocatable)
        return None

    def target_vram(index: ChunkIndex, target: Key) -> int | None:
        name = vanilla_name(target) if target in index else (target[1] if target[0] == "g" else None)
        return vs.vram_by_name.get(name) if name is not None else None

    def internal_offsets(index: ChunkIndex, chunk: Chunk, sec) -> set[int]:
        return {
            r.offset for r in chunk.relocs if section_of(target_vram(index, r.target)) == sec
        }

    in_place: dict[Key, tuple[str, list]] = {}
    candidates = set()
    for key, chunk in selected.items():
        vname = vanilla_name(key)
        b = base.get(key)
        if chunk.is_func or chunk.kind not in ("data", "rodata") or vname is None or b is None:
            continue
        if b.size != chunk.size or any(r.type != R_MIPS_32 for r in chunk.relocs):
            continue
        sec = section_of(vs.vram_by_name.get(vname))
        if sec is None:
            continue
        if sec[2] and internal_offsets(current, chunk, sec) != internal_offsets(base, b, sec):
            # Overlay data: pointers within the overlay are relocated by the vanilla relocations
            continue
        candidates.add(key)

    def in_place_relocs(key: Key, size: int | None = None):
        relocs = []
        for r in current.get(key).relocs:
            if size is not None and r.offset >= size:
                continue
            t = r.target
            tc = current.get(t)
            if tc is None:
                if t[0] != "g":
                    return None
                relocs.append((r.offset, t[1], r.addend))
                continue
            tname = None if tc.is_anon else vanilla_name(t)
            if tname is None or base.get(t) is None:
                return None
            if not tc.is_func and t in selected and t not in candidates and t != key:
                # Points to data copied into the mod
                return None
            relocs.append((r.offset, tname, r.addend))
        return relocs

    closed: set[Key] = set()
    protected_users: dict[Key, set[Key]] = {}
    while True:
        changed = False
        for key in sorted(candidates, key=str):
            if in_place_relocs(key) is None:
                candidates.discard(key)
                changed = True
        worklist = [
            k
            for k, c in selected.items()
            # Data copied into the mod, and functions without vanilla counterpart (e.g. gcc's
            # func.part.0 clones), which can only be reached through patched users
            if (not c.is_func or vanilla_name(k) is None) and k not in candidates and k not in closed
        ]
        while worklist:
            data_key = worklist.pop()
            closed.add(data_key)
            for user_key in users.get(data_key, ()):
                user = current.get(user_key)
                if user is None:
                    continue
                if user_key in selected:
                    if user_key in candidates:
                        # Uses a copy: can't stay at its vanilla address
                        candidates.discard(user_key)
                        changed = True
                    continue
                if user.is_anon:
                    # Anonymous chunks are copied with their users anyway, propagate to their users
                    if user_key not in closed:
                        worklist.append(user_key)
                    continue
                if user.is_func and vanilla_name(user_key) in protected and base.get(user_key) is not None:
                    protected_users.setdefault(data_key, set()).add(user_key)
                    continue
                selected[user_key] = user
                reasons[user_key] = f"uses {_key_str(data_key)}"
                changed = True
                if not user.is_func or vanilla_name(user_key) is None:
                    worklist.append(user_key)
        if not changed:
            break

    for key in candidates:
        in_place[key] = (vanilla_name(key), in_place_relocs(key), selected[key].size)
        del selected[key]

    # Protected functions using data copied into the mod keep using the vanilla data
    for data_key, user_keys in sorted(protected_users.items(), key=lambda kv: str(kv[0])):
        chunk = current.get(data_key)
        b = base.get(data_key)
        vname = vanilla_name(data_key)
        relocs = None
        if (
            not chunk.is_anon
            and vname is not None
            and b is not None
            and chunk.kind in ("data", "rodata")
            and b.kind == chunk.kind
            and chunk.size >= b.size
            and all(r.type == R_MIPS_32 for r in chunk.relocs)
            and not (section_of(vs.vram_by_name.get(vname)) or (0, 0, True))[2]
        ):
            relocs = in_place_relocs(data_key, b.size)
        user_names = ", ".join(sorted(vanilla_name(k) for k in user_keys))
        if relocs is None:
            # Can't keep the vanilla data up to date: patch the users (reported as conflicts)
            for user_key in user_keys:
                if user_key not in selected:
                    selected[user_key] = current.get(user_key)
                    reasons[user_key] = f"uses {_key_str(data_key)}"
            continue
        in_place[data_key] = (vname, relocs, b.size)
        warnings.append(
            f"{user_names} (already patched by the recomp) keep using the vanilla {vname}, whose original"
            f" {b.size:#x} bytes are updated in place; the mod's copy is {chunk.size:#x} bytes"
            + (" and the two copies are not kept in sync at runtime" if chunk.is_writable else "")
        )

    # 4. Pull in referenced chunks without vanilla counterpart
    externals: dict[Key, str] = {}
    pending = list(selected)
    while pending:
        key = pending.pop()
        chunk = selected[key]
        for r in chunk.relocs:
            target = r.target
            if target in selected:
                continue
            target_chunk = current.get(target)
            if target_chunk is None:
                # Undefined in the current objects: must be a vanilla symbol (or asset, ROM address)
                if target[0] != "g":
                    raise Exception(f"Unresolved reference to {target} from {_key_str(key)}")
                externals[target] = target[1]
                continue
            name = None if target_chunk.is_anon else vanilla_name(target)
            if name is not None and base.get(target) is not None:
                externals[target] = name
                continue
            # No vanilla counterpart: copy it into the mod
            if target_chunk.is_writable and not target_chunk.is_anon and target[0] == "s" and "." not in target[2]:
                warnings.append(
                    f"Copying mutable static {target[2]} ({target[1]}) into the mod because"
                    " its vanilla counterpart could not be found"
                )
            selected[target] = target_chunk
            reasons[target] = f"needed by {_key_str(key)}"
            pending.append(target)

    # 5. Names and roles
    patches: set[Key] = set()
    names: dict[Key, str] = {}
    used_names: set[str] = set()
    for key, chunk in selected.items():
        vname = None if chunk.is_anon else vanilla_name(key)
        if chunk.is_func and vname is not None and vname in vs.func_names and base.get(key) is not None:
            patches.add(key)
            names[key] = vname
        elif chunk.is_anon:
            names[key] = f"__anon_{_sanitize(chunk.tu)}_{_sanitize(chunk.name)}"
        elif key[0] == "g" and key[1] not in vs.vram_by_name:
            names[key] = key[1]
        elif key[0] == "g":
            # Copy of vanilla data (changed): give it a distinct name
            names[key] = f"{key[1]}__mod"
        else:
            names[key] = f"{_sanitize(key[2])}__{_sanitize(key[1])}"
        assert names[key] not in used_names, (names[key], key)
        used_names.add(names[key])

    return Selection(
        chunks=selected,
        patches=patches,
        names=names,
        externals=externals,
        reasons=reasons,
        warnings=warnings,
        in_place=in_place,
    )


def _key_str(key: Key) -> str:
    if key[0] == "g":
        return key[1]
    if key[0] == "s":
        return f"{key[2]} ({key[1]})"
    return f"<anon {key[2]} ({key[1]})>"


def load_objects(objs: dict[str, Path], build_dir: str) -> dict[str, ObjectFile]:
    from .objfile import tu_name

    result = {}
    for spec_obj, path in objs.items():
        tu = tu_name(spec_obj, build_dir)
        result[tu] = ObjectFile(path, tu)
    return result
