#!/usr/bin/env python3

# SPDX-FileCopyrightText: © 2025 ZeldaRET
# SPDX-License-Identifier: CC0-1.0

"""
Disassemble a MM cutscene script (CutsceneData[]) into cutscene command macros.

Every emitted macro is checked to encode back to the original data.
When a macro would not reproduce the original data (for example because of unexpected
values in unused fields), a more generic macro or raw words are emitted instead.
"""

import argparse
import functools
import math
from pathlib import Path
import re
import struct
from typing import Callable, Optional

ROOT_DIR = Path(__file__).parent.parent
INCLUDE_DIR = ROOT_DIR / "include"

INDENT = " " * 4


"""
Enums, parsed from the headers
"""


ENUM_RE = re.compile(r"typedef\s+enum\s*\w*\s*\{(?P<body>[^}]*)\}\s*(?P<name>\w+)\s*;")


@functools.cache
def parse_header_enums(header_name: str) -> dict[str, dict[int, str]]:
    """Parse all `typedef enum [tag] { ... } EnumName;` from a header in include/

    Returns a dict mapping enum names to dicts mapping values to enumerator names.
    """
    text = (INCLUDE_DIR / header_name).read_text()
    values_by_name: dict[str, int] = {}
    enums: dict[str, dict[int, str]] = {}
    for m in ENUM_RE.finditer(text):
        body = m.group("body")
        # remove comments
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        body = re.sub(r"//[^\n]*", "", body)
        names_by_value: dict[int, str] = {}
        next_value = 0
        for item in body.split(","):
            item = item.strip()
            if not item:
                continue
            if "=" in item:
                name, value_str = (s.strip() for s in item.split("=", 1))
                if value_str in values_by_name:
                    value = values_by_name[value_str]
                else:
                    try:
                        value = int(value_str, 0)
                    except ValueError:
                        # Unsupported expression, ignore this enum
                        names_by_value = None
                        break
            else:
                name = item
                value = next_value
            values_by_name[name] = value
            names_by_value.setdefault(value, name)
            next_value = value + 1
        if names_by_value is not None:
            enums[m.group("name")] = names_by_value
    return enums


def parse_enum(header_name: str, enum_name: str) -> dict[int, str]:
    enums = parse_header_enums(header_name)
    if enum_name not in enums:
        raise Exception(f"enum {enum_name} not found (or not parsable) in {header_name}")
    return enums[enum_name]


@functools.cache
def get_seq_ids() -> dict[int, str]:
    """NA_BGM_* names, from include/tables/sequence_table.h"""
    text = (INCLUDE_DIR / "tables" / "sequence_table.h").read_text()
    names = re.findall(r"^\s*DEFINE_SEQUENCE(?:_PTR)?\s*\(\s*\w+\s*,\s*(\w+)\s*,", text, re.M)
    return {i: name for i, name in enumerate(names)}


def enum_name(header_name: str, enum_name_: str, value: int, *, signed16=False):
    names = parse_enum(header_name, enum_name_)
    if signed16 and value >= 0x8000:
        value -= 0x10000
    return names.get(value)


"""
Value helpers
"""


def s16(v: int):
    return v - 0x10000 if v >= 0x8000 else v


def s32(v: int):
    return v - 0x1_0000_0000 if v >= 0x8000_0000 else v


def fmt_s16_hex(v: int):
    """Format a u16 as a hex number, using a negative for values >= 0x8000"""
    v = s16(v)
    return f"-0x{-v:04X}" if v < 0 else f"0x{v:04X}"


def float_from_word(w: int) -> float:
    return struct.unpack(">f", struct.pack(">I", w))[0]


def word_from_float(f: float) -> int:
    return struct.unpack(">I", struct.pack(">f", f))[0]


def fmt_float(w: int):
    """Format a word interpreted as a float in a way that round-trips to the same word"""
    f = float_from_word(w)
    if math.isnan(f) or math.isinf(f):
        return None
    for precision in range(1, 18):
        s = f"{f:.{precision}g}"
        if word_from_float(float(s)) == w:
            break
    else:
        return None
    if "e" not in s and "." not in s:
        s += ".0"
    return s + "f"


def BBBB(a, b, c, d):
    return ((a & 0xFF) << 24) | ((b & 0xFF) << 16) | ((c & 0xFF) << 8) | (d & 0xFF)


def BBH(a, b, c):
    return ((a & 0xFF) << 24) | ((b & 0xFF) << 16) | (c & 0xFFFF)


def HBB(a, b, c):
    return ((a & 0xFFFF) << 16) | ((b & 0xFF) << 8) | (c & 0xFF)


def HH(a, b):
    return ((a & 0xFFFF) << 16) | (b & 0xFFFF)


def W(a):
    return a & 0xFFFF_FFFF


def H0(w):
    return w >> 16


def H1(w):
    return w & 0xFFFF


def B(w, i):
    return (w >> (24 - 8 * i)) & 0xFF


def fmt_raw_words(words: list[int]):
    return ", ".join(f"{{ CMD_W(0x{w:08X}) }}" for w in words)


"""
Commands
"""

CS_CMD_TEXT = 0x0A
CS_CMD_CAMERA_SPLINE = 0x5A
CS_CMD_MISC = 0x96
CS_CMD_LIGHT_SETTING = 0x97
CS_CMD_TRANSITION = 0x98
CS_CMD_MOTION_BLUR = 0x99
CS_CMD_GIVE_TATL = 0x9A
CS_CMD_TRANSITION_GENERAL = 0x9B
CS_CMD_FADE_OUT_SEQ = 0x9C
CS_CMD_TIME = 0x9D
CS_CMD_PLAYER_CUE = 0xC8
CS_CMD_START_SEQ = 0x12C
CS_CMD_STOP_SEQ = 0x12D
CS_CMD_START_AMBIENCE = 0x12E
CS_CMD_FADE_OUT_AMBIENCE = 0x12F
CS_CMD_SFX_REVERB_INDEX_2 = 0x130
CS_CMD_SFX_REVERB_INDEX_1 = 0x131
CS_CMD_MODIFY_SEQ = 0x132
CS_CMD_DESTINATION = 0x15E
CS_CMD_CHOOSE_CREDITS_SCENES = 0x15F
CS_CMD_RUMBLE = 0x190


def is_actor_cue_cmd(cmd: int):
    return 100 <= cmd <= 149 or cmd == 201 or 450 <= cmd <= 599


# A macro candidate: (source string, encoded words)
MacroCandidate = tuple[str, list[int]]


def first_matching(words: list[int], candidates: list[Optional[MacroCandidate]]):
    for candidate in candidates:
        if candidate is None:
            continue
        src, enc = candidate
        if enc == words:
            return src
    return None


def name_or(names_value: Optional[str], fallback: str):
    return names_value if names_value is not None else fallback


# 8-byte entries


def entry8_unk_data(words):
    a, s, e, d = H0(words[0]), H1(words[0]), H0(words[1]), H1(words[1])
    return (
        f"CS_UNK_DATA({fmt_s16_hex(a)}, {s16(s)}, {s16(e)}, {fmt_s16_hex(d)})",
        [HH(a, s), HH(e, d)],
    )


def entry8_typed(macro: str, type_str: Callable[[int], Optional[str]]):
    """For macros of the form MACRO(type, startFrame, endFrame) encoding as HH(type, start), HH(end, end)"""

    def f(words):
        t, s, e = H0(words[0]), H1(words[0]), H0(words[1])
        t_str = type_str(t)
        if t_str is None:
            t_str = str(s16(t))
        return (f"{macro}({t_str}, {s16(s)}, {s16(e)})", [HH(t, s), HH(e, e)])

    return f


def entry8_misc(words):
    t, s, e, u = H0(words[0]), H1(words[0]), H0(words[1]), H1(words[1])
    t_str = name_or(
        enum_name("z64cutscene.h", "CutsceneMiscType", t), fmt_s16_hex(t)
    )
    return (f"CS_MISC({t_str}, {s16(s)}, {s16(e)}, {fmt_s16_hex(u)})", [HH(t, s), HH(e, u)])


def entry8_light_setting(words):
    b0, b1, s, e = B(words[0], 0), B(words[0], 1), H1(words[0]), H0(words[1])
    return (
        f"CS_LIGHT_SETTING({b1 - 1}, {s16(s)}, {s16(e)})",
        [BBH(0, b1, s), HH(e, e)],
    )


def seq_id_str(seq_id_plus_one: int):
    seq_id = seq_id_plus_one - 1
    name = get_seq_ids().get(seq_id)
    if name is None:
        return f"0x{seq_id:02X}"
    return name


def entry8_start_seq(words):
    i, s, e = H0(words[0]), H1(words[0]), H0(words[1])
    return (
        f"CS_START_SEQ({seq_id_str(i)}, {s16(s)}, {s16(e)})",
        [HH(i, s), HH(e, e)] if i != 0 else None,
    )


def entry8_stop_seq(words):
    i, s, e, u = H0(words[0]), H1(words[0]), H0(words[1]), H1(words[1])
    return (
        f"CS_STOP_SEQ({seq_id_str(i)}, {s16(s)}, {s16(e)}, {s16(u)})",
        [HH(i, s), HH(e, u)] if i != 0 else None,
    )


def entry8_unused0(macro: str):
    """For macros of the form MACRO(unused0, startFrame, endFrame) encoding as HH(unused0, start), HH(end, 0)"""

    def f(words):
        u, s, e = H0(words[0]), H1(words[0]), H0(words[1])
        return (
            f"{macro}({fmt_s16_hex(u)}, {s16(s)}, {s16(e)})",
            [HH(u, s), HH(e, 0)],
        )

    return f


def entry8_give_tatl(words):
    t, s, e = H0(words[0]), H1(words[0]), H0(words[1])
    t_str = {0: "false", 1: "true"}.get(t, str(t))
    return (f"CS_GIVE_TATL({t_str}, {s16(s)}, {s16(e)})", [HH(t, s), HH(e, e)])


# 12-byte entries


def entry12_text(words):
    text_id, s = H0(words[0]), H1(words[0])
    e, t = H0(words[1]), H1(words[1])
    a1, a2 = H0(words[2]), H1(words[2])
    candidates: list[Optional[MacroCandidate]] = []
    enc = [HH(text_id, s), HH(e, t), HH(a1, a2)]
    if t == 0xFFFF and text_id == 0xFFFF and a1 == 0xFFFF and a2 == 0xFFFF:
        candidates.append((f"CS_TEXT_NONE({s16(s)}, {s16(e)})", enc))
    if t == 0:
        candidates.append(
            (
                f"CS_TEXT_DEFAULT(0x{text_id:X}, {s16(s)}, {s16(e)}, 0x{a1:X}, 0x{a2:X})",
                enc,
            )
        )
    if t == 1:
        candidates.append(
            (
                f"CS_TEXT_TYPE_1(0x{text_id:X}, {s16(s)}, {s16(e)}, 0x{a1:X}, 0x{a2:X})",
                enc,
            )
        )
    if t == 2 and a2 == 0xFFFF:
        action = name_or(
            enum_name("z64ocarina.h", "OcarinaSongActionId", text_id), f"0x{text_id:X}"
        )
        candidates.append(
            (f"CS_TEXT_OCARINA_ACTION({action}, {s16(s)}, {s16(e)}, 0x{a1:X})", enc)
        )
    if t == 3:
        candidates.append(
            (
                f"CS_TEXT_TYPE_3(0x{text_id:X}, {s16(s)}, {s16(e)}, 0x{a1:X}, 0x{a2:X})",
                enc,
            )
        )
    if t == 4 and a2 == 0xFFFF:
        candidates.append(
            (
                f"CS_TEXT_BOSSES_REMAINS(0x{text_id:X}, {s16(s)}, {s16(e)}, 0x{a1:X})",
                enc,
            )
        )
    if t == 5 and a2 == 0xFFFF:
        candidates.append(
            (
                f"CS_TEXT_ALL_NORMAL_MASKS(0x{text_id:X}, {s16(s)}, {s16(e)}, 0x{a1:X})",
                enc,
            )
        )
    t_str = name_or(
        enum_name("z64cutscene.h", "CutsceneTextType", t, signed16=True), str(s16(t))
    )
    candidates.append(
        (
            f"CS_TEXT(0x{text_id:X}, {s16(s)}, {s16(e)}, {t_str}, 0x{a1:X}, 0x{a2:X})",
            enc,
        )
    )
    return first_matching(words, candidates)


def entry12_fade_out_seq(words):
    p, s, e = H0(words[0]), H1(words[0]), H0(words[1])
    p_str = name_or(
        enum_name("z64cutscene.h", "CutsceneFadeOutSeqPlayer", p), str(s16(p))
    )
    return first_matching(
        words,
        [
            (
                f"CS_FADE_OUT_SEQ({p_str}, {s16(s)}, {s16(e)})",
                [HH(p, s), HH(e, 0), W(0)],
            )
        ],
    )


def entry12_transition_general(words):
    t, s = H0(words[0]), H1(words[0])
    e, r, g = H0(words[1]), B(words[1], 2), B(words[1], 3)
    b = B(words[2], 0)
    t_str = name_or(
        enum_name("z64cutscene.h", "CsTransitionGeneralType", t), fmt_s16_hex(t)
    )
    return first_matching(
        words,
        [
            (
                f"CS_TRANSITION_GENERAL({t_str}, {s16(s)}, {s16(e)}, {r}, {g}, {b})",
                [HH(t, s), HBB(e, r, g), BBBB(b, 0, 0, 0)],
            )
        ],
    )


def entry12_time(words):
    u, s = H0(words[0]), H1(words[0])
    e, h, m = H0(words[1]), B(words[1], 2), B(words[1], 3)
    return first_matching(
        words,
        [
            (
                f"CS_TIME({fmt_s16_hex(u)}, {s16(s)}, {s16(e)}, {h}, {m})",
                [HH(u, s), HBB(e, h, m), W(0)],
            )
        ],
    )


def entry12_rumble(words):
    t, s = H0(words[0]), H1(words[0])
    e, intensity, decay_timer = H0(words[1]), B(words[1], 2), B(words[1], 3)
    decay_step = B(words[2], 0)
    t_str = name_or(
        enum_name("z64cutscene.h", "CutsceneRumbleType", t), fmt_s16_hex(t)
    )
    return first_matching(
        words,
        [
            (
                f"CS_RUMBLE({t_str}, {s16(s)}, {s16(e)}, 0x{intensity:02X}, 0x{decay_timer:02X}, 0x{decay_step:02X})",
                [HH(t, s), HBB(e, intensity, decay_timer), BBBB(decay_step, 0, 0, 0)],
            )
        ],
    )


# 0x30-byte entries (actor and player cues)


def entry48_cue(macro: str, id_str: Callable[[int], str]):
    def f(words):
        cue_id, s = H0(words[0]), H1(words[0])
        e, rot_x = H0(words[1]), H1(words[1])
        rot_y, rot_z = H0(words[2]), H1(words[2])
        positions = [s32(w) for w in words[3:9]]
        normals = [fmt_float(w) for w in words[9:12]]
        if any(n is None for n in normals):
            return None
        normals_str = ", ".join(
            f"CS_FLOAT(0x{w:08X}, {n})" for w, n in zip(words[9:12], normals)
        )
        return (
            f"{macro}({id_str(cue_id)}, {s16(s)}, {s16(e)}, "
            f"0x{rot_x:04X}, 0x{rot_y:04X}, 0x{rot_z:04X}, "
            + ", ".join(str(p) for p in positions)
            + f", {normals_str})"
        )

    return f


def player_cue_id_str(v: int):
    return name_or(enum_name("z64player.h", "PlayerCueId", v), str(v))


"""
Lists
"""

# cmd: (list macro name, entry size in words, entry disassembler)
# The entry disassembler returns either a (source, encoded words) candidate
# (which is checked against the data), or directly a source string (already checked),
# or None if no macro matches.
LIST_COMMANDS = {
    CS_CMD_TEXT: ("CS_TEXT_LIST", 3, entry12_text),
    CS_CMD_MISC: ("CS_MISC_LIST", 2, entry8_misc),
    CS_CMD_LIGHT_SETTING: ("CS_LIGHT_SETTING_LIST", 2, entry8_light_setting),
    CS_CMD_TRANSITION: (
        "CS_TRANSITION_LIST",
        2,
        entry8_typed(
            "CS_TRANSITION",
            lambda t: enum_name("z64cutscene.h", "CutsceneTransitionType", t),
        ),
    ),
    CS_CMD_MOTION_BLUR: (
        "CS_MOTION_BLUR_LIST",
        2,
        entry8_typed(
            "CS_MOTION_BLUR",
            lambda t: enum_name("z64cutscene.h", "CsMotionBlurType", t),
        ),
    ),
    CS_CMD_GIVE_TATL: ("CS_GIVE_TATL_LIST", 2, entry8_give_tatl),
    CS_CMD_TRANSITION_GENERAL: (
        "CS_TRANSITION_GENERAL_LIST",
        3,
        entry12_transition_general,
    ),
    CS_CMD_FADE_OUT_SEQ: ("CS_FADE_OUT_SEQ_LIST", 3, entry12_fade_out_seq),
    CS_CMD_TIME: ("CS_TIME_LIST", 3, entry12_time),
    CS_CMD_START_SEQ: ("CS_START_SEQ_LIST", 2, entry8_start_seq),
    CS_CMD_STOP_SEQ: ("CS_STOP_SEQ_LIST", 2, entry8_stop_seq),
    CS_CMD_START_AMBIENCE: (
        "CS_START_AMBIENCE_LIST",
        2,
        entry8_unused0("CS_START_AMBIENCE"),
    ),
    CS_CMD_FADE_OUT_AMBIENCE: (
        "CS_FADE_OUT_AMBIENCE_LIST",
        2,
        entry8_unused0("CS_FADE_OUT_AMBIENCE"),
    ),
    CS_CMD_SFX_REVERB_INDEX_2: (
        "CS_SFX_REVERB_INDEX_2_LIST",
        2,
        entry8_unused0("CS_SFX_REVERB_INDEX_2"),
    ),
    CS_CMD_SFX_REVERB_INDEX_1: (
        "CS_SFX_REVERB_INDEX_1_LIST",
        2,
        entry8_unused0("CS_SFX_REVERB_INDEX_1"),
    ),
    CS_CMD_MODIFY_SEQ: (
        "CS_MODIFY_SEQ_LIST",
        2,
        entry8_typed(
            "CS_MODIFY_SEQ",
            lambda t: enum_name("z64cutscene.h", "CsModifySeqType", t),
        ),
    ),
    CS_CMD_DESTINATION: (
        "CS_DESTINATION_LIST",
        2,
        entry8_typed(
            "CS_DESTINATION",
            lambda t: enum_name("z64cutscene.h", "CsDestinationType", t),
        ),
    ),
    CS_CMD_CHOOSE_CREDITS_SCENES: (
        "CS_CHOOSE_CREDITS_SCENES_LIST",
        2,
        entry8_typed(
            "CS_CHOOSE_CREDITS_SCENES",
            lambda t: enum_name("z64cutscene.h", "CsChooseCreditsSceneType", t),
        ),
    ),
    CS_CMD_RUMBLE: ("CS_RUMBLE_LIST", 3, entry12_rumble),
}


def disassemble_entry(entry_dis, words: list[int]):
    """Returns the source for an entry, falling back to generic forms if needed"""
    result = entry_dis(words)
    src = None
    if isinstance(result, tuple):
        candidate_src, enc = result
        if enc is not None and enc == words:
            src = candidate_src
    elif isinstance(result, str):
        src = result
    if src is None:
        if len(words) == 2:
            src, enc = entry8_unk_data(words)
            assert enc == words
        else:
            src = fmt_raw_words(words)
    return src


class CutsceneDisassemblyError(Exception):
    pass


def disassemble_cam_spline(words: list[int], i: int, num_bytes: int):
    """Disassemble the camera spline data starting at words[i] (after the numBytes word)

    Returns the lines and the amount of words consumed, or None if the data could not be parsed
    """
    lines = []
    start = i
    end = i + num_bytes // 4
    if num_bytes % 4 != 0 or end > len(words):
        return None
    while i < end:
        if H0(words[i]) == 0xFFFF:
            if words[i] != HH(0xFFFF, 4):
                return None
            lines.append("CS_CAM_END()")
            i += 1
            break
        if i + 2 > end:
            return None
        num_entries, unused0 = H0(words[i]), H1(words[i])
        unused1, duration = H0(words[i + 1]), H1(words[i + 1])
        lines.append(
            f"CS_CAM_SPLINE({num_entries}, 0x{unused0:04X}, 0x{unused1:04X}, {s16(duration)})"
        )
        i += 2
        if i + num_entries * (3 + 3 + 2) > end:
            return None
        for kind in ("at", "eye"):
            for _ in range(num_entries):
                w = words[i : i + 3]
                interp_type, weight, point_duration = B(w[0], 0), B(w[0], 1), H1(w[0])
                x, y = H0(w[1]), H1(w[1])
                z, rel_to = H0(w[2]), H1(w[2])
                interp_str = name_or(
                    enum_name("z64cutscene.h", "CutsceneCamInterpType", interp_type),
                    str(interp_type),
                )
                rel_to_str = name_or(
                    enum_name("z64cutscene.h", "CutsceneCamRelativeTo", rel_to),
                    str(rel_to),
                )
                lines.append(
                    f"CS_CAM_POINT({interp_str}, {weight}, {s16(point_duration)}, "
                    f"{s16(x)}, {s16(y)}, {s16(z)}, {rel_to_str})"
                )
                i += 3
        for _ in range(num_entries):
            unused0_, roll = H0(words[i]), H1(words[i])
            fov, unused1_ = H0(words[i + 1]), H1(words[i + 1])
            lines.append(
                f"CS_CAM_MISC(0x{unused0_:04X}, 0x{roll:04X}, {s16(fov)}, 0x{unused1_:04X})"
            )
            i += 2
    if i != end:
        return None
    return lines, i - start


def disassemble_cutscene(cs_in: list[int]) -> tuple[int, str]:
    """
    Takes a sequence of words cs_in (the cutscene data)

    Returns the size of the cutscene data in words, and the disassembled source.
    The source is a series of indented lines, each ending with a comma and a newline.
    """
    words = [w & 0xFFFF_FFFF for w in cs_in]
    lines: list[str] = []

    if len(words) < 2:
        raise CutsceneDisassemblyError("Not enough data")

    total_entries = s32(words[0])
    frame_count = s32(words[1])
    lines.append(f"CS_BEGIN_CUTSCENE({total_entries}, {frame_count})")
    i = 2

    for _ in range(total_entries):
        if i >= len(words):
            raise CutsceneDisassemblyError("Reached end of data")
        cmd = words[i]

        if cmd == 0xFFFF_FFFF:
            lines.append("CS_END()")
            i += 1
            break

        if i + 1 >= len(words):
            raise CutsceneDisassemblyError("Reached end of data")
        arg = words[i + 1]

        if cmd == CS_CMD_CAMERA_SPLINE:
            num_bytes = arg
            result = disassemble_cam_spline(words, i + 2, num_bytes)
            lines.append(f"CS_CAM_SPLINE_LIST({num_bytes})")
            if result is None:
                n = num_bytes // 4
                if num_bytes % 4 != 0 or i + 2 + n > len(words):
                    raise CutsceneDisassemblyError("Invalid camera spline data")
                lines.extend(fmt_raw_words([w]) for w in words[i + 2 : i + 2 + n])
                i += 2 + n
            else:
                spline_lines, n = result
                lines.extend(INDENT + line for line in spline_lines)
                i += 2 + n
            continue

        num_entries = s32(arg)
        if num_entries < 0 or num_entries > 0x1000:
            raise CutsceneDisassemblyError(
                f"Unreasonable number of entries {num_entries} for command 0x{cmd:X}"
            )

        if is_actor_cue_cmd(cmd) or cmd == CS_CMD_PLAYER_CUE:
            entry_size = 12
            if cmd == CS_CMD_PLAYER_CUE:
                lines.append(f"CS_PLAYER_CUE_LIST({num_entries})")
                entry_dis = entry48_cue("CS_PLAYER_CUE", player_cue_id_str)
            else:
                cmd_name = name_or(
                    enum_name("z64cutscene.h", "CutsceneCmd", cmd), f"0x{cmd:03X}"
                )
                lines.append(f"CS_ACTOR_CUE_LIST({cmd_name}, {num_entries})")
                entry_dis = entry48_cue("CS_ACTOR_CUE", lambda v: str(v))
        elif cmd in LIST_COMMANDS:
            list_macro, entry_size, entry_dis = LIST_COMMANDS[cmd]
            lines.append(f"{list_macro}({num_entries})")
        else:
            # Unimplemented commands are skipped by the code, with entries of 8 bytes
            cmd_name = name_or(
                enum_name("z64cutscene.h", "CutsceneCmd", cmd), f"0x{cmd:X}"
            )
            lines.append(f"CS_UNK_DATA_LIST({cmd_name}, {num_entries})")
            entry_size = 2
            entry_dis = entry8_unk_data

        i += 2
        for _ in range(num_entries):
            if i + entry_size > len(words):
                raise CutsceneDisassemblyError("Reached end of data")
            entry_words = words[i : i + entry_size]
            lines.append(INDENT + disassemble_entry(entry_dis, entry_words))
            i += entry_size
    else:
        # All command lists have been read, the end marker is expected next
        if i < len(words) and words[i] == 0xFFFF_FFFF:
            lines.append("CS_END()")
            i += 1

    return i, "".join(INDENT + line + ",\n" for line in lines)


def main():
    parser = argparse.ArgumentParser(description="Disassembles cutscenes for MM")
    parser.add_argument("file", type=Path, help="file containing the cutscene data")
    parser.add_argument(
        "offset", type=lambda s: int(s, 16), help="offset of the cutscene in the file (hex)"
    )
    args = parser.parse_args()

    data = args.file.read_bytes()[args.offset :]
    data = data[: len(data) // 4 * 4]
    cs_data = [w for (w,) in struct.iter_unpack(">I", data)]
    _, src = disassemble_cutscene(cs_data)
    print(f"CutsceneData D_{args.offset:08X}[] = {{")
    print(src, end="")
    print("};")


if __name__ == "__main__":
    main()
