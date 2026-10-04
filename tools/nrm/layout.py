# SPDX-FileCopyrightText: © 2025 ZeldaRET
# SPDX-License-Identifier: CC0-1.0

"""Build the ROM used for the mod's ROM patch (patch.bps).

Recomp runs vanilla code (recompiled ahead of time), and finds overlay code by vanilla ROM address.
So this ROM keeps:
- every vanilla file at its vanilla address
- code files (boot, code, overlays) with their vanilla content: code changes are in the mod's code.
  Only changed data that keeps the same layout is patched in place in them (code_patches).
and only differs from the vanilla ROM in data files (objects, scenes, textures, audio, text, ...):
- changed data files are written in place if they fit, otherwise moved to the end of the ROM
- new data files are added at the end of the ROM
- dmadata (the file table) is updated
"""

import dataclasses
import re
import struct
import subprocess
from pathlib import Path

import crunch64

from .spec import Segment


def align(v: int, a: int = 0x10) -> int:
    return (v + a - 1) // a * a


def read_symbols(elf: Path, nm: str = "mips-linux-gnu-nm") -> dict[str, int]:
    out = subprocess.run([nm, str(elf)], capture_output=True, text=True, check=True).stdout
    syms = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            syms[parts[2]] = int(parts[0], 16)
    return syms


def read_symbol_sections(elf: Path) -> dict[str, str]:
    """Map symbol name -> name of the ELF section it is defined in"""
    from elftools.elf.elffile import ELFFile

    result = {}
    with open(elf, "rb") as f:
        e = ELFFile(f)
        sections = [s.name for s in e.iter_sections()]
        symtab = e.get_section_by_name(".symtab")
        for sym in symtab.iter_symbols():
            shndx = sym["st_shndx"]
            if isinstance(shndx, int) and sym.name:
                result[sym.name] = sections[shndx]
    return result


@dataclasses.dataclass
class DmaEntry:
    vrom_start: int
    vrom_end: int
    rom_start: int
    rom_end: int  # 0 if uncompressed

    @property
    def is_compressed(self):
        return self.rom_end != 0

    def pack(self) -> bytes:
        return struct.pack(">IIII", self.vrom_start, self.vrom_end, self.rom_start, self.rom_end)


def read_dmadata(rom: bytes, offset: int) -> list[DmaEntry]:
    entries = []
    while True:
        e = DmaEntry(*struct.unpack_from(">IIII", rom, offset + 16 * len(entries)))
        if e.vrom_end == 0:
            break
        entries.append(e)
    return entries


@dataclasses.dataclass
class LayoutResult:
    rom: bytes
    # Values in the recomp game of ROM address symbols (_xSegmentRomStart, ...) that differ from vanilla
    # or are new
    rom_symbols: dict[str, int]
    # Names of the changed or new data segments
    changed_segments: list[str]
    new_segments: list[str]
    # New code segments (code goes in the mod, not in the ROM)
    new_code_segments: list[str]
    # If dmadata had to be moved: number of entries (including the end marker)
    dmadata_relocated_entries: int | None
    report: list[str]


ROM_SYMBOL_RE = re.compile(r"^_(.+)Segment(RomStart|RomEnd)(Temp)?$")


def read_data_relocations(elf: Path, segment_names: set[str]) -> dict[str, list[tuple[int, str, int]]]:
    """Read the R_MIPS_32 relocations of the given segments in a linked ELF (linked with --emit-relocs).

    Returns segment name -> list of (offset in segment, symbol name, addend)
    """
    from elftools.elf.elffile import ELFFile
    from elftools.elf.relocation import RelocationSection

    result: dict[str, list[tuple[int, str, int]]] = {}
    with open(elf, "rb") as f:
        e = ELFFile(f)
        sections = list(e.iter_sections())
        symtab = e.get_section_by_name(".symtab")
        for rel in sections:
            if not isinstance(rel, RelocationSection):
                continue
            target = sections[rel["sh_info"]]
            if not target.name.startswith(".."):
                continue
            seg_name = target.name[2:]
            if seg_name not in segment_names:
                continue
            data = target.data()
            base = target["sh_addr"]
            entries = []
            for r in rel.iter_relocations():
                r_type = r["r_info_type"]
                offset = r["r_offset"] - base
                sym = symtab.get_symbol(r["r_info_sym"])
                if r_type != 2:  # R_MIPS_32
                    raise Exception(
                        f"Unsupported relocation type {r_type} in data file {seg_name} at 0x{offset:X} ({sym.name})"
                    )
                value = struct.unpack_from(">I", data, offset)[0]
                addend = (value - sym["st_value"]) & 0xFFFFFFFF
                entries.append((offset, sym.name, addend))
            result[seg_name] = entries
    return result


def build_rom(
    vanilla_rom_compressed: bytes,
    vanilla_rom_uncompressed: bytes,
    vanilla_syms: dict[str, int],
    vanilla_code_segments: set[str],
    dmadata_capacity: int,
    current_rom: bytes,
    current_elf: Path,
    current_syms: dict[str, int],
    current_symbol_sections: dict[str, str],
    current_segments: list[Segment],
    code_patches: dict[str, list[tuple[int, bytes]]] = {},
) -> LayoutResult:
    """code_patches: segment name -> list of (offset in the file, bytes) to write in vanilla code files"""
    report = []

    dmadata_vrom = vanilla_syms["_dmadataSegmentRomStart"]
    dma = read_dmadata(vanilla_rom_compressed, dmadata_vrom)
    dma_by_vrom = {e.vrom_start: i for i, e in enumerate(dma)}

    rom = bytearray(vanilla_rom_compressed)
    vrom_end = max(e.vrom_end for e in dma)
    rom_end = max(
        (e.rom_end if e.is_compressed else e.rom_start + e.vrom_end - e.vrom_start)
        for e in dma
        if e.rom_start != 0xFFFFFFFF  # files not present in the ROM
    )
    vrom_end = align(vrom_end, 0x1000)
    rom_end = align(max(rom_end, len(rom)), 0x1000)

    rom_symbols: dict[str, int] = {}
    changed_segments = []
    new_segments = []
    new_code_segments = []

    # 1. Collect data files, assign VROM addresses
    @dataclasses.dataclass
    class File:
        name: str
        data: bytearray
        compress: bool
        dma_index: int | None  # index of the vanilla dmadata entry
        vrom_start: int
        is_new: bool
        is_code: bool = False

    files: list[File] = []
    for seg in current_segments:
        name = seg.name
        if name == "dmadata":
            continue
        rom_start_sym = f"_{name}SegmentRomStart"
        rom_end_sym = f"_{name}SegmentRomEnd"
        if rom_start_sym not in current_syms:
            continue
        data = bytearray(current_rom[current_syms[rom_start_sym] : current_syms[rom_end_sym]])
        is_code = name in vanilla_code_segments or current_syms.get(f"_{name}SegmentTextSize", 0) > 0

        if rom_start_sym in vanilla_syms:
            v_start = vanilla_syms[rom_start_sym]
            v_end = vanilla_syms[rom_end_sym]
            index = dma_by_vrom.get(v_start)
            if is_code:
                # Code files keep their vanilla content (changes are in the mod code), except for data
                # patched in place
                if name in code_patches:
                    assert index is not None, name
                    data = bytearray(vanilla_rom_uncompressed[v_start:v_end])
                    for offset, patch in code_patches[name]:
                        assert offset + len(patch) <= len(data), (name, offset)
                        data[offset : offset + len(patch)] = patch
                    files.append(File(name, data, dma[index].is_compressed, index, v_start, False, True))
                continue
            if index is None or v_end == v_start:
                # Not a file (e.g. NOLOAD segment)
                continue
            if len(data) <= v_end - v_start:
                vrom_start = v_start
            else:
                vrom_start = vrom_end
                vrom_end = align(vrom_end + len(data), 0x1000)
                report.append(f"{name}: grew from 0x{v_end - v_start:X} to 0x{len(data):X} bytes, moved")
            files.append(File(name, data, dma[index].is_compressed, index, vrom_start, False))
        else:
            if is_code:
                # New code (e.g. a new actor overlay): its code goes in the mod, as an internal actor
                new_code_segments.append(name)
                for suffix in ("RomStart", "RomEnd", "RomStartTemp", "RomEndTemp"):
                    rom_symbols[f"_{name}Segment{suffix}"] = 0
                continue
            if len(data) == 0:
                continue
            vrom_start = vrom_end
            vrom_end = align(vrom_end + len(data), 0x1000)
            files.append(File(name, data, seg.compress, None, vrom_start, True))

    for file in files:
        name = file.name
        if file.is_code:
            continue
        new_start, new_end = file.vrom_start, file.vrom_start + len(file.data)
        if file.is_new or (new_start, new_end) != (
            vanilla_syms[f"_{name}SegmentRomStart"],
            vanilla_syms[f"_{name}SegmentRomEnd"],
        ):
            rom_symbols[f"_{name}SegmentRomStart"] = new_start
            rom_symbols[f"_{name}SegmentRomEnd"] = new_end
            rom_symbols[f"_{name}SegmentRomStartTemp"] = new_start
            rom_symbols[f"_{name}SegmentRomEndTemp"] = new_end

    # 2. Fix up the addresses in data files: ROM addresses use the layout above,
    #    and references to code use vanilla addresses.
    relocs = read_data_relocations(current_elf, {f.name for f in files if not f.is_code})
    for file in files:
        for offset, sym_name, addend in relocs.get(file.name, []):
            value = None
            m = ROM_SYMBOL_RE.match(sym_name)
            if m is not None:
                value = rom_symbols.get(sym_name, vanilla_syms.get(sym_name))
                if value is None:
                    raise Exception(f"{file.name}: unknown ROM symbol {sym_name}")
            else:
                section = current_symbol_sections.get(sym_name, "")
                seg = section[2:] if section.startswith("..") else section
                seg = seg.removesuffix(".bss")
                if seg in vanilla_code_segments or current_syms.get(sym_name, 0) >= 0x80000000:
                    value = vanilla_syms.get(sym_name)
                    if value is None:
                        raise Exception(
                            f"Data file {file.name} references {sym_name}, which doesn't exist in the vanilla"
                            " game. References from data files to new code are not supported."
                        )
            if value is not None:
                struct.pack_into(">I", file.data, offset, (value + addend) & 0xFFFFFFFF)

    # 3. Write the changed and new files
    for file in files:
        if not file.is_new:
            v_start = vanilla_syms[f"_{file.name}SegmentRomStart"]
            v_end = vanilla_syms[f"_{file.name}SegmentRomEnd"]
            if vanilla_rom_uncompressed[v_start:v_end] == file.data:
                continue
            changed_segments.append(file.name)
            slot = dma[file.dma_index]
        else:
            new_segments.append(file.name)
            slot = None
        payload = crunch64.yaz0.compress(bytes(file.data)) if file.compress else bytes(file.data)
        size = align(len(payload))
        r_start = None
        if slot is not None and slot.rom_start != 0xFFFFFFFF:
            slot_start = slot.rom_start
            slot_end = slot.rom_end if slot.is_compressed else slot.rom_start + slot.vrom_end - slot.vrom_start
            if size <= align(slot_end) - slot_start:
                r_start = slot_start
                rom[slot_start : align(slot_end)] = bytes(align(slot_end) - slot_start)
        if r_start is None:
            r_start = rom_end
            rom_end = align(r_start + size, 0x10)
        if len(rom) < r_start + size:
            rom.extend(bytes(r_start + size - len(rom)))
        rom[r_start : r_start + len(payload)] = payload
        entry = DmaEntry(
            file.vrom_start,
            file.vrom_start + len(file.data),
            r_start,
            r_start + len(payload) if file.compress else 0,
        )
        if slot is not None:
            dma[file.dma_index] = entry
        else:
            dma.append(entry)

    # 4. dmadata
    dmadata_index = dma_by_vrom[dmadata_vrom]
    n_entries = len(dma) + 1  # with end marker
    dmadata_relocated_entries = None
    old = dma[dmadata_index]
    old_size = old.vrom_end - old.vrom_start
    dmadata_capacity = min(dmadata_capacity, old_size // 16)
    if n_entries <= dmadata_capacity:
        # Keep the vanilla size (padded with zeros), so nothing referencing it needs to change
        dma_bytes = b"".join(e.pack() for e in dma)
        dma_bytes += bytes(old_size - len(dma_bytes))
        rom[old.rom_start : old.rom_start + old_size] = dma_bytes
    else:
        # dmadata is loaded to a fixed address in RAM, right before the code segment, so it can't grow
        # past its capacity: move it, along with the table in RAM (see the mod code).
        # The game reads dmadata from its ROM address as a physical address: put it where both match.
        addr = align(max(vrom_end, rom_end), 0x1000)
        size = 16 * n_entries
        dma[dmadata_index] = DmaEntry(addr, addr + size, addr, 0)
        dma_bytes = b"".join(e.pack() for e in dma) + bytes(16)
        assert len(dma_bytes) == size
        if len(rom) < addr + size:
            rom.extend(bytes(addr + size - len(rom)))
        rom[addr : addr + size] = dma_bytes
        rom_symbols["_dmadataSegmentRomStart"] = addr
        rom_symbols["_dmadataSegmentRomEnd"] = addr + size
        rom_symbols["_dmadataSegmentRomStartTemp"] = addr
        rom_symbols["_dmadataSegmentRomEndTemp"] = addr + size
        dmadata_relocated_entries = n_entries
        report.append(f"dmadata: {n_entries} entries exceed the capacity of {dmadata_capacity}, moved")

    rom = bytearray(rom[: align(len(rom), 0x1000)])
    _update_crc(rom)

    return LayoutResult(
        rom=bytes(rom),
        rom_symbols=rom_symbols,
        changed_segments=changed_segments,
        new_segments=new_segments,
        new_code_segments=new_code_segments,
        dmadata_relocated_entries=dmadata_relocated_entries,
        report=report,
    )


def _update_crc(rom: bytearray):
    import ipl3checksum

    result = ipl3checksum.CICKind.CIC_X105.calculateChecksum(bytes(rom))
    if result is not None:
        crc1, crc2 = result
        struct.pack_into(">II", rom, 0x10, crc1, crc2)
