# SPDX-FileCopyrightText: © 2025 ZeldaRET
# SPDX-License-Identifier: CC0-1.0

"""Write the selected chunks into a single relocatable MIPS ELF object for the mod.

- Patches of vanilla functions go in .recomp_patch
- Special mod sections (.recomp_hook.*, .recomp_export, ...) are kept as-is
- Everything else goes in .text.*, .data.*, .rodata.*, .bss.*
- References to vanilla symbols are undefined symbols, resolved by RecompModTool using the
  recomp symbol files.
"""

import struct
from pathlib import Path

from .diff import Selection
from .objfile import R_MIPS_26, R_MIPS_32, R_MIPS_HI16, R_MIPS_LO16, sign_extend_16

ELF_FLAGS_MIPS2_O32 = 0x10001001

SHT_PROGBITS = 1
SHT_SYMTAB = 2
SHT_STRTAB = 3
SHT_NOBITS = 8
SHT_REL = 9

SHF_WRITE = 0x1
SHF_ALLOC = 0x2
SHF_EXECINSTR = 0x4
SHF_INFO_LINK = 0x40
SHF_GNU_RETAIN = 0x200000

STB_LOCAL = 0
STB_GLOBAL = 1
STT_NOTYPE = 0
STT_OBJECT = 1
STT_FUNC = 2
STT_SECTION = 3


class _StrTab:
    def __init__(self):
        self.data = bytearray(b"\0")
        self.offsets = {"": 0}

    def add(self, s: str) -> int:
        if s not in self.offsets:
            self.offsets[s] = len(self.data)
            self.data += s.encode() + b"\0"
        return self.offsets[s]


def _out_section_name(chunk, name: str, is_patch: bool, is_force_patch: bool) -> str:
    if is_force_patch:
        return ".recomp_force_patch"
    if is_patch:
        return ".recomp_patch"
    if chunk.kind.startswith("."):
        return chunk.section
    if chunk.kind == "text":
        return f".text.{name}"
    return f".{chunk.kind}.{name}"


def write_mod_object(selection: Selection, out_path: Path, force_patches: set = frozenset()):
    """force_patches: keys of patches that replace functions already patched by the base recomp"""
    shstrtab = _StrTab()
    strtab = _StrTab()

    # Sections: (name, type, flags, align, data(bytes) or size for NOBITS, chunk key)
    sections = []
    chunk_section_index: dict = {}

    keys = sorted(selection.chunks, key=lambda k: (str(k)))
    for key in keys:
        chunk = selection.chunks[key]
        name = selection.names[key]
        is_patch = key in selection.patches
        sec_name = _out_section_name(chunk, name, is_patch, key in force_patches)
        if chunk.kind == "bss":
            sh_type = SHT_NOBITS
            flags = SHF_ALLOC | SHF_WRITE
        else:
            sh_type = SHT_PROGBITS
            flags = SHF_ALLOC
            if chunk.is_func or chunk.kind == "text" or (
                chunk.kind.startswith(".") and chunk.is_func
            ):
                flags |= SHF_EXECINSTR
            elif chunk.kind == "data":
                flags |= SHF_WRITE
        if is_patch or chunk.kind.startswith("."):
            flags |= SHF_GNU_RETAIN
        data = bytearray(chunk.data) if sh_type != SHT_NOBITS else None
        sections.append(
            {
                "name": sec_name,
                "type": sh_type,
                "flags": flags,
                "align": max(chunk.align, 4 if chunk.is_func else 1),
                "data": data,
                "size": chunk.size,
                "key": key,
            }
        )
        # Section indices start at 1 (0 is the null section)
        chunk_section_index[key] = len(sections)

    # Symbols: null, then one global symbol per chunk, then undefined externals
    symbols = [(0, 0, 0, 0, 0, 0)]  # name, value, size, info, other, shndx
    symbol_index: dict = {}
    # Local symbols must come first: we only use global symbols (besides null)
    for key in keys:
        chunk = selection.chunks[key]
        name = selection.names[key]
        st_type = STT_FUNC if chunk.is_func else STT_OBJECT
        symbol_index[key] = len(symbols)
        symbols.append(
            (
                strtab.add(name),
                0,
                chunk.size,
                (STB_GLOBAL << 4) | st_type,
                0,
                chunk_section_index[key],
            )
        )
    for key, ext_name in sorted(selection.externals.items(), key=lambda kv: kv[1]):
        if ext_name in symbol_index:
            symbol_index[key] = symbol_index[ext_name]
            continue
        idx = len(symbols)
        symbol_index[key] = idx
        symbol_index[ext_name] = idx
        symbols.append((strtab.add(ext_name), 0, 0, (STB_GLOBAL << 4) | STT_NOTYPE, 0, 0))

    # Relocations, writing addends into the relocated fields
    rel_sections = []
    for sec_i, sec in enumerate(sections, start=1):
        chunk = selection.chunks[sec["key"]]
        if not chunk.relocs:
            continue
        data = sec["data"]
        assert data is not None, "relocations in bss"
        entries = []
        for r in chunk.relocs:
            sym_i = symbol_index.get(r.target)
            if sym_i is None:
                raise Exception(f"No symbol for relocation target {r.target} in {selection.names[sec['key']]}")
            A = r.addend & 0xFFFFFFFF
            word = struct.unpack_from(">I", data, r.offset)[0]
            if r.type == R_MIPS_32:
                word = A
            elif r.type == R_MIPS_26:
                word = (word & ~0x3FFFFFF) | ((A >> 2) & 0x3FFFFFF)
            elif r.type == R_MIPS_HI16:
                word = (word & ~0xFFFF) | (((A + 0x8000) >> 16) & 0xFFFF)
            elif r.type == R_MIPS_LO16:
                word = (word & ~0xFFFF) | (A & 0xFFFF)
            struct.pack_into(">I", data, r.offset, word & 0xFFFFFFFF)
            entries.append((r.offset, (sym_i << 8) | r.type, r.type, sym_i, r.addend))
        _check_hilo_pairs(entries, data, selection.names[sec["key"]])
        rel_data = b"".join(struct.pack(">II", off, info) for off, info, _, _, _ in entries)
        rel_sections.append((sec_i, rel_data))

    # Assemble the ELF file
    out_sections = []  # (name_off, type, flags, data/size, link, info, align, entsize)
    for sec in sections:
        out_sections.append(
            [
                shstrtab.add(sec["name"]),
                sec["type"],
                sec["flags"],
                bytes(sec["data"]) if sec["data"] is not None else sec["size"],
                0,
                0,
                sec["align"],
                0,
            ]
        )
    symtab_index = len(out_sections) + len(rel_sections) + 1
    for target_i, rel_data in rel_sections:
        target_name = sections[target_i - 1]["name"]
        out_sections.append(
            [shstrtab.add(".rel" + target_name), SHT_REL, SHF_INFO_LINK, rel_data, symtab_index, target_i, 4, 8]
        )
    symtab_data = b"".join(
        struct.pack(">IIIBBH", n, v, sz, info, other, shndx) for n, v, sz, info, other, shndx in symbols
    )
    strtab_index = symtab_index + 1
    out_sections.append([shstrtab.add(".symtab"), SHT_SYMTAB, 0, symtab_data, strtab_index, 1, 4, 16])
    out_sections.append([shstrtab.add(".strtab"), SHT_STRTAB, 0, bytes(strtab.data), 0, 0, 1, 0])
    shstrtab_name = shstrtab.add(".shstrtab")
    out_sections.append([shstrtab_name, SHT_STRTAB, 0, None, 0, 0, 1, 0])
    out_sections[-1][3] = bytes(shstrtab.data)

    # Layout
    offset = 0x34
    body = bytearray()
    headers = [struct.pack(">IIIIIIIIII", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)]
    for name, sh_type, flags, content, link, info, align, entsize in out_sections:
        if sh_type == SHT_NOBITS:
            size = content
            file_off = offset + len(body)
        else:
            pad = (-(offset + len(body))) % max(align, 1)
            body += b"\0" * pad
            file_off = offset + len(body)
            body += content
            size = len(content)
        headers.append(struct.pack(">IIIIIIIIII", name, sh_type, flags, 0, file_off, size, link, info, align, entsize))
    pad = (-(offset + len(body))) % 4
    body += b"\0" * pad
    shoff = offset + len(body)
    shnum = len(headers)
    shstrndx = shnum - 1
    ehdr = struct.pack(
        ">16sHHIIIIIHHHHHH",
        b"\x7fELF\x01\x02\x01" + b"\0" * 9,
        1,  # ET_REL
        8,  # EM_MIPS
        1,
        0,
        0,
        shoff,
        ELF_FLAGS_MIPS2_O32,
        0x34,
        0,
        0,
        40,
        shnum,
        shstrndx,
    )
    out_path.write_bytes(ehdr + bytes(body) + b"".join(headers))


def _check_hilo_pairs(entries, data, where: str):
    """Check that the HI16/LO16 pairs compute the expected addresses, like the linker does"""
    last_hi: dict[int, int] = {}
    for off, _, r_type, sym_i, addend in entries:
        word = struct.unpack_from(">I", data, off)[0]
        if r_type == R_MIPS_HI16:
            last_hi[sym_i] = word & 0xFFFF
        elif r_type == R_MIPS_LO16 and sym_i in last_hi:
            computed = ((last_hi[sym_i] << 16) + sign_extend_16(word & 0xFFFF)) & 0xFFFFFFFF
            if computed != addend & 0xFFFFFFFF:
                raise Exception(
                    f"{where}: HI16/LO16 pair at 0x{off:X} doesn't compute the expected addend"
                    f" (0x{computed:X} != 0x{addend & 0xFFFFFFFF:X})"
                )
