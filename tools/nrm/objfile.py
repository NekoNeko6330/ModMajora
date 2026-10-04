# SPDX-FileCopyrightText: © 2025 ZeldaRET
# SPDX-License-Identifier: CC0-1.0

"""Read relocatable MIPS ELF objects (compiled with -ffunction-sections -fdata-sections)
into "chunks": one chunk per function or data symbol, plus anonymous chunks
(e.g. merged string literals), each with its bytes and its relocations.

Relocation targets are expressed as symbolic keys, which makes it possible to compare
chunks from different compilations independently of their addresses.
"""

import dataclasses
import hashlib
from pathlib import Path
import struct

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection
from elftools.elf.constants import SH_FLAGS

R_MIPS_NONE = 0
R_MIPS_32 = 2
R_MIPS_26 = 4
R_MIPS_HI16 = 5
R_MIPS_LO16 = 6

SUPPORTED_RELOCS = {R_MIPS_32, R_MIPS_26, R_MIPS_HI16, R_MIPS_LO16}

# Keys:
#   ("g", name)              global symbol
#   ("s", tu, name)          static (file-local) symbol of translation unit tu
#   ("a", tu, section_name)  anonymous chunk (section without symbols, e.g. .rodata.cst4)
Key = tuple


@dataclasses.dataclass
class Reloc:
    offset: int  # offset in the chunk
    type: int
    target: Key  # key of the target chunk (or of an undefined global)
    addend: int  # offset from the start of the target
    # Key used for comparing chunks: same as target, except for anonymous constant data,
    # which is identified by content (e.g. the string literal) rather than location.
    cmp_target: Key = None
    cmp_addend: int = 0


@dataclasses.dataclass
class Chunk:
    key: Key
    tu: str
    name: str  # symbol name, or section name for anonymous chunks
    is_func: bool
    is_global: bool
    section: str  # original section name
    kind: str  # "text", "data", "rodata", "bss", or the section name for special sections
    data: bytes  # empty for bss
    size: int
    align: int
    relocs: list[Reloc]
    # For anonymous merged sections (strings or constants)
    merge_entsize: int = 0
    merge_strings: bool = False
    # offset of the chunk in its section
    start: int = 0
    # other symbol names at the same address
    aliases: list = dataclasses.field(default_factory=list)

    @property
    def is_anon(self):
        return self.key[0] == "a"

    @property
    def is_writable(self):
        return self.kind in ("data", "bss")

    def normalized_data(self) -> bytes:
        """Bytes with relocated fields zeroed"""
        if not self.data:
            return b""
        data = bytearray(self.data)
        for r in self.relocs:
            word = struct.unpack_from(">I", data, r.offset)[0]
            if r.type == R_MIPS_32:
                word = 0
            elif r.type == R_MIPS_26:
                word &= ~0x3FFFFFF
            elif r.type in (R_MIPS_HI16, R_MIPS_LO16):
                word &= ~0xFFFF
            struct.pack_into(">I", data, r.offset, word & 0xFFFFFFFF)
        return bytes(data)


def sign_extend_16(v: int):
    return v - 0x10000 if v & 0x8000 else v


def classify_section(name: str, flags: int, sh_type: str) -> str:
    if sh_type == "SHT_NOBITS":
        return "bss"
    if name == ".text" or name.startswith(".text."):
        return "text"
    if name == ".rodata" or name.startswith(".rodata."):
        return "rodata"
    if name == ".data" or name.startswith(".data."):
        return "data"
    if name == ".bss" or name.startswith(".bss."):
        return "bss"
    if flags & SH_FLAGS.SHF_EXECINSTR:
        # Special sections such as .recomp_hook.FuncName
        return name
    if flags & SH_FLAGS.SHF_WRITE:
        return "data"
    return "rodata" if not name.startswith(".recomp") else name


IGNORED_SECTIONS = {
    ".reginfo",
    ".MIPS.abiflags",
    ".pdr",
    ".comment",
    ".note.GNU-stack",
    ".gnu.attributes",
    ".mdebug.abi32",
}


class ObjectFile:
    def __init__(self, path: Path, tu: str):
        self.path = path
        self.tu = tu
        self.chunks: list[Chunk] = []
        self._parse()

    def _parse(self):
        with open(self.path, "rb") as f:
            elf = ELFFile(f)
            assert elf.little_endian is False
            symtab = elf.get_section_by_name(".symtab")
            sections = list(elf.iter_sections())
            symbols = list(symtab.iter_symbols()) if symtab is not None else []

            # Chunks by section index: list of (start, end, chunk)
            self._chunks_by_section: dict[int, list[Chunk]] = {}
            section_data: dict[int, bytes] = {}

            # Defined symbols per section
            syms_by_section: dict[int, list] = {}
            for sym in symbols:
                shndx = sym["st_shndx"]
                if not isinstance(shndx, int):
                    continue
                st_type = sym["st_info"]["type"]
                if st_type in ("STT_SECTION", "STT_FILE"):
                    continue
                if not sym.name or sym.name.startswith("$"):
                    # Unnamed, or compiler-generated local labels (e.g. $LC0 for constants),
                    # whose numbering isn't stable: treated as anonymous data
                    continue
                syms_by_section.setdefault(shndx, []).append(sym)

            for index, sec in enumerate(sections):
                name = sec.name
                flags = sec["sh_flags"]
                if not (flags & SH_FLAGS.SHF_ALLOC):
                    continue
                if name in IGNORED_SECTIONS:
                    continue
                size = sec["sh_size"]
                sh_type = sec["sh_type"]
                data = b"" if sh_type == "SHT_NOBITS" else sec.data()
                section_data[index] = data
                kind = classify_section(name, flags, sh_type)
                align = max(1, sec["sh_addralign"])
                syms = sorted(
                    syms_by_section.get(index, []), key=lambda s: s["st_value"]
                )
                chunks = []
                if size == 0 and not syms:
                    continue
                if not syms:
                    chunk = Chunk(
                        key=("a", self.tu, name),
                        tu=self.tu,
                        name=name,
                        is_func=False,
                        is_global=False,
                        section=name,
                        kind=kind,
                        data=data,
                        size=size,
                        align=align,
                        relocs=[],
                    )
                    chunk.start = 0
                    if flags & SH_FLAGS.SHF_MERGE:
                        chunk.merge_entsize = sec["sh_entsize"]
                        chunk.merge_strings = bool(flags & SH_FLAGS.SHF_STRINGS)
                    chunks.append(chunk)
                else:
                    # Deduplicate aliases (several symbols at the same address): keep globals first
                    by_addr: dict[int, list] = {}
                    for sym in syms:
                        by_addr.setdefault(sym["st_value"], []).append(sym)
                    addrs = sorted(by_addr)
                    if addrs[0] != 0:
                        # Leading anonymous bytes
                        addrs.insert(0, 0)
                        by_addr[0] = []
                    for i, addr in enumerate(addrs):
                        end = addrs[i + 1] if i + 1 < len(addrs) else size
                        cands = by_addr[addr]
                        if not cands:
                            key = ("a", self.tu, f"{name}+{addr:X}")
                            sym_name = f"{name}+{addr:X}"
                            is_global = False
                            is_func = False
                        else:
                            cands.sort(
                                key=lambda s: (
                                    s["st_info"]["bind"] == "STB_LOCAL",
                                    s.name,
                                )
                            )
                            sym = cands[0]
                            is_global = sym["st_info"]["bind"] != "STB_LOCAL"
                            is_func = sym["st_info"]["type"] == "STT_FUNC"
                            sym_name = sym.name
                            key = (
                                ("g", sym_name)
                                if is_global
                                else ("s", self.tu, sym_name)
                            )
                        chunk = Chunk(
                            key=key,
                            tu=self.tu,
                            name=sym_name,
                            is_func=is_func or kind == "text",
                            is_global=is_global,
                            section=name,
                            kind=kind,
                            data=data[addr:end] if data else b"",
                            size=end - addr,
                            align=align if addr == 0 else 4,
                            relocs=[],
                        )
                        chunk.start = addr
                        chunk.aliases = [s.name for s in cands[1:]]
                        chunks.append(chunk)
                self._chunks_by_section[index] = chunks
                self.chunks.extend(chunks)

            # Relocations
            for rel_sec in elf.iter_sections():
                if not isinstance(rel_sec, RelocationSection):
                    continue
                if rel_sec.is_RELA():
                    raise Exception(f"{self.path}: RELA relocations are not supported")
                target_index = rel_sec["sh_info"]
                if target_index not in self._chunks_by_section:
                    continue
                self._parse_relocs(
                    list(rel_sec.iter_relocations()),
                    target_index,
                    symbols,
                    sections,
                    section_data,
                )

    def _chunk_at(self, section_index: int, offset: int) -> Chunk:
        chunks = self._chunks_by_section.get(section_index)
        if not chunks:
            raise Exception(
                f"{self.path}: relocation into section {section_index} which has no chunk"
            )
        # Note: offsets may be out of the section bounds (e.g. `array - 1` in code),
        # in which case the nearest chunk is used.
        found = chunks[0]
        for c in chunks:
            if c.start <= offset:
                found = c
        return found

    def _parse_relocs(self, relocs, section_index, symbols, sections, section_data):
        data = section_data[section_index]

        # Compute full addends (REL: addends are stored in the relocated fields)
        entries = []
        for rel in relocs:
            r_type = rel["r_info_type"]
            if r_type == R_MIPS_NONE:
                continue
            if r_type not in SUPPORTED_RELOCS:
                raise Exception(
                    f"{self.path}: unsupported relocation type {r_type} at"
                    f" {sections[section_index].name}+0x{rel['r_offset']:X}"
                )
            entries.append((rel["r_offset"], r_type, rel["r_info_sym"]))

        addends = [0] * len(entries)
        for i, (offset, r_type, sym_index) in enumerate(entries):
            word = struct.unpack_from(">I", data, offset)[0]
            if r_type == R_MIPS_32:
                addends[i] = word
            elif r_type == R_MIPS_26:
                addends[i] = (word & 0x3FFFFFF) << 2
            elif r_type == R_MIPS_HI16:
                hi = word & 0xFFFF
                # Find the paired LO16 (next LO16 with the same symbol)
                lo = None
                for j in range(i + 1, len(entries)):
                    if entries[j][1] == R_MIPS_LO16 and entries[j][2] == sym_index:
                        lo_word = struct.unpack_from(">I", data, entries[j][0])[0]
                        lo = sign_extend_16(lo_word & 0xFFFF)
                        break
                if lo is None:
                    raise Exception(f"{self.path}: unpaired R_MIPS_HI16 at 0x{offset:X}")
                addends[i] = ((hi << 16) + lo) & 0xFFFFFFFF
            elif r_type == R_MIPS_LO16:
                lo = sign_extend_16(word & 0xFFFF)
                # Use the most recent HI16 with the same symbol
                hi = None
                for j in range(i - 1, -1, -1):
                    if entries[j][1] == R_MIPS_HI16 and entries[j][2] == sym_index:
                        hi_word = struct.unpack_from(">I", data, entries[j][0])[0]
                        hi = hi_word & 0xFFFF
                        break
                if hi is None:
                    hi = 0
                addends[i] = ((hi << 16) + lo) & 0xFFFFFFFF

        for (offset, r_type, sym_index), addend in zip(entries, addends):
            chunk = self._chunk_at(section_index, offset)
            sym = symbols[sym_index]
            addend = addend - 0x1_0000_0000 if addend & 0x8000_0000 else addend
            target, target_addend = self._resolve_target(sym, addend, sections, section_data)
            reloc = Reloc(
                offset=offset - chunk.start,
                type=r_type,
                target=target,
                addend=target_addend,
            )
            chunk.relocs.append(reloc)

    def _resolve_target(self, sym, addend, sections, section_data):
        shndx = sym["st_shndx"]
        st_type = sym["st_info"]["type"]
        if st_type == "STT_SECTION":
            target_chunk = self._chunk_at(shndx, addend)
            return target_chunk.key, addend - target_chunk.start
        if shndx == "SHN_UNDEF" or not isinstance(shndx, int):
            return ("g", sym.name), addend
        if sym["st_info"]["bind"] != "STB_LOCAL":
            return ("g", sym.name), addend
        if sym.name.startswith("$") or not sym.name:
            # Compiler-generated label, handled like section-relative references
            target_chunk = self._chunk_at(shndx, sym["st_value"] + addend)
            return target_chunk.key, sym["st_value"] + addend - target_chunk.start
        # Local named symbol: target the chunk containing it
        target_chunk = self._chunk_at(shndx, sym["st_value"])
        return target_chunk.key, addend + sym["st_value"] - target_chunk.start


def anon_cmp_key(chunk: Chunk, addend: int) -> tuple[Key, int]:
    """Key identifying a reference into an anonymous chunk by content"""
    if chunk.merge_strings and chunk.data:
        end = chunk.data.find(b"\0", addend)
        if end < 0:
            end = len(chunk.data)
        return ("str", chunk.data[addend:end]), 0
    if chunk.merge_entsize and chunk.data:
        n = chunk.merge_entsize
        start = addend - addend % n
        return ("cst", chunk.data[start : start + n]), addend % n
    return ("anon", chunk_signature_noanon(chunk)), addend


def chunk_signature_noanon(chunk: Chunk) -> str:
    h = hashlib.sha1()
    h.update(chunk.kind.encode())
    h.update(chunk.size.to_bytes(4, "big"))
    h.update(chunk.normalized_data())
    for r in chunk.relocs:
        h.update(repr((r.offset, r.type, r.target, r.addend)).encode())
    return h.hexdigest()


def tu_name(spec_obj: str, build_dir: str) -> str:
    """Translation unit name from a spec object path, e.g. src/code/z_actor"""
    prefix = build_dir + "/"
    assert spec_obj.startswith(prefix), (spec_obj, build_dir)
    return spec_obj[len(prefix) : -len(".o")]
