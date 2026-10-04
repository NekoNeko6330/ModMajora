# SPDX-FileCopyrightText: © 2025 ZeldaRET
# SPDX-License-Identifier: CC0-1.0

"""Resources for MM keyframe skeletons and animations (z64keyframe.h)"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..extase.memorymap import MemoryContext

from ..extase import (
    File,
    ResourceParseWaiting,
)
from ..extase.cdata_resources import (
    CDataResource,
    CDataArrayResource,
    CDataExt_Struct,
    CDataExt_Value,
    CDataExtWriteContext,
    cdata_ext_Vec3s,
)

from . import dlist_resources

Z64HDRPRFX = "z64"


def write_draw_flags(v: int):
    flags = ["KEYFRAME_DRAW_XLU" if v & 1 else "KEYFRAME_DRAW_OPA"]
    rest = v & ~1
    if rest:
        flags.append(f"0x{rest:02X}")
    return " | ".join(flags)


def write_segmented_pointer(
    resource, memory_context: "MemoryContext", v, wctx: CDataExtWriteContext
):
    assert isinstance(v, int)
    wctx.f.write(wctx.line_prefix)
    if v == 0:
        wctx.f.write("NULL")
    else:
        wctx.f.write(memory_context.get_c_reference_at_segmented(v))
    return True


#
# Skeletons
#


class KeyFrameStandardLimbArrayResource(CDataArrayResource):
    elem_cdata_ext = CDataExt_Struct(
        (
            ("dList", dlist_resources.cdata_ext_gfx_segmented),
            ("numChildren", CDataExt_Value.u8),
            ("drawFlags", CDataExt_Value("B").set_write_str_v(write_draw_flags)),
            ("jointPos", cdata_ext_Vec3s),
        )
    )

    def get_c_declaration_base(self):
        return f"KeyFrameStandardLimb {self.symbol_name}[]"

    def get_c_includes(self):
        return (Z64HDRPRFX + "keyframe.h",)

    def get_h_includes(self):
        return (Z64HDRPRFX + "keyframe.h",)


class KeyFrameFlexLimbArrayResource(CDataArrayResource):
    elem_cdata_ext = CDataExt_Struct(
        (
            ("dList", dlist_resources.cdata_ext_gfx_segmented),
            ("numChildren", CDataExt_Value.u8),
            ("drawFlags", CDataExt_Value("B").set_write_str_v(write_draw_flags)),
            (
                "callbackIndex",
                CDataExt_Value("B").set_write_str_v(
                    lambda v: "KF_CALLBACK_INDEX_NONE" if v == 0xFF else str(v)
                ),
            ),
            ("pad7", CDataExt_Value.pad8),
        )
    )

    def get_c_declaration_base(self):
        return f"KeyFrameFlexLimb {self.symbol_name}[]"

    def get_c_includes(self):
        return (Z64HDRPRFX + "keyframe.h",)

    def get_h_includes(self):
        return (Z64HDRPRFX + "keyframe.h",)


class KeyFrameSkeletonResource(CDataResource):
    def report_limbs(resource: "KeyFrameSkeletonResource", memory_context, v):
        assert isinstance(v, int)
        address = v
        limbs_type = (
            KeyFrameFlexLimbArrayResource
            if resource.is_flex
            else KeyFrameStandardLimbArrayResource
        )
        limbs = memory_context.report_resource_at_segmented(
            resource,
            address,
            limbs_type,
            lambda file, offset: limbs_type(
                file, offset, f"{resource.name}_{address:08X}_Limbs"
            ),
        )
        limbs.set_length(resource.cdata_unpacked["limbCount"])

    def write_limbCount(
        resource: "KeyFrameSkeletonResource", memory_context, v, wctx
    ):
        wctx.f.write(wctx.line_prefix)
        address = resource.cdata_unpacked["limbs"]
        wctx.f.write(memory_context.get_c_expression_length_at_segmented(address))
        return True

    cdata_ext = CDataExt_Struct(
        (
            ("limbCount", CDataExt_Value("B").set_write(write_limbCount)),
            ("dListCount", CDataExt_Value.u8),
            ("pad2", CDataExt_Value.pad16),
            (
                "limbs",
                CDataExt_Value("I")
                .set_report(report_limbs)
                .set_write(write_segmented_pointer),
            ),
        )
    )

    def __init__(self, file: File, range_start: int, name: str, is_flex: bool):
        super().__init__(file, range_start, name)
        self.is_flex = is_flex

    def get_c_declaration_base(self):
        if self.is_flex:
            return f"KeyFrameFlexSkeleton {self.symbol_name}"
        return f"KeyFrameSkeleton {self.symbol_name}"

    def get_c_reference(self, resource_offset: int):
        if resource_offset == 0:
            return f"&{self.symbol_name}"
        raise ValueError

    def get_c_includes(self):
        return ("array_count.h",)

    def get_h_includes(self):
        return (Z64HDRPRFX + "keyframe.h",)


#
# Animations
#


class KeyFrameArrayResource(CDataArrayResource):
    elem_cdata_ext = CDataExt_Struct(
        (
            ("frame", CDataExt_Value.s16),
            ("value", CDataExt_Value.s16),
            ("velocity", CDataExt_Value.s16),
        )
    )

    def write_extracted(self, memory_context):
        with self.extract_to_path.open("w") as f:
            if not self.braces_in_source:
                f.write("{\n")
            f.write("    // { frame, value, velocity }\n")
            for v in self.cdata_unpacked:
                f.write(f"    {{ {v['frame']:5}, {v['value']:6}, {v['velocity']:6} }},\n")
            if not self.braces_in_source:
                f.write("}\n")

    def get_c_declaration_base(self):
        return f"KeyFrame {self.symbol_name}[]"

    def get_h_includes(self):
        return (Z64HDRPRFX + "keyframe.h",)


class _IntArrayResource(CDataArrayResource):
    c_type: str
    per_line = 8

    def fmt(self, v: int) -> str:
        return str(v)

    def write_extracted(self, memory_context):
        with self.extract_to_path.open("w") as f:
            if not self.braces_in_source:
                f.write("{\n")
            values = self.cdata_unpacked
            for i in range(0, len(values), self.per_line):
                f.write("    ")
                f.write(", ".join(self.fmt(v) for v in values[i : i + self.per_line]))
                f.write(",\n")
            if not self.braces_in_source:
                f.write("}\n")

    def get_c_declaration_base(self):
        return f"{self.c_type} {self.symbol_name}[]"

    def get_h_includes(self):
        return ("ultra64.h",)


class KeyFrameS16ArrayResource(_IntArrayResource):
    elem_cdata_ext = CDataExt_Value.s16
    c_type = "s16"


class KeyFrameBitFlagsStandardResource(_IntArrayResource):
    elem_cdata_ext = CDataExt_Value.u8
    c_type = "u8"

    def fmt(self, v):
        return f"0x{v:02X}"


class KeyFrameBitFlagsFlexResource(_IntArrayResource):
    elem_cdata_ext = CDataExt_Value.u16
    c_type = "u16"

    def fmt(self, v):
        return f"0x{v:03X}"


def _read_segmented(memory_context: "MemoryContext", address: int, size: int):
    resolve_result = memory_context.resolve_segmented(address)
    data = resolve_result.file.data
    assert data is not None
    start = resolve_result.file_offset
    assert start + size <= len(data), (hex(address), size)
    return data[start : start + size]


def _popcount(v: int):
    return bin(v).count("1")


class KeyFrameAnimationResource(CDataResource):
    def report_animation(resource: "KeyFrameAnimationResource", memory_context, v):
        assert isinstance(v, dict)
        import struct

        limb_count = resource.limb_count
        assert limb_count is not None

        # bit flags
        if resource.is_flex:
            bit_flags_data = _read_segmented(
                memory_context, v["bitFlags"], 2 * limb_count
            )
            bit_flags = [b for (b,) in struct.iter_unpack(">H", bit_flags_data)]
            num_kf_nums = sum(_popcount(b & 0x1FF) for b in bit_flags)
            num_values = 9 * limb_count
            bit_flags_type = KeyFrameBitFlagsFlexResource
        else:
            bit_flags = list(_read_segmented(memory_context, v["bitFlags"], limb_count))
            num_kf_nums = _popcount(bit_flags[0] & 0b111000) + sum(
                _popcount(b & 0b111) for b in bit_flags
            )
            num_values = 3 + 3 * limb_count
            bit_flags_type = KeyFrameBitFlagsStandardResource
        num_fixed_values = num_values - num_kf_nums

        kf_nums = [
            n
            for (n,) in struct.iter_unpack(
                ">h", _read_segmented(memory_context, v["kfNums"], 2 * num_kf_nums)
            )
        ]
        num_key_frames = sum(kf_nums)

        def report_array(address: int, resource_type, name_suffix: str, length: int):
            if address == 0 or length == 0:
                return
            array = memory_context.report_resource_at_segmented(
                resource,
                address,
                resource_type,
                lambda file, offset: resource_type(
                    file, offset, f"{resource.name}_{address:08X}_{name_suffix}"
                ),
            )
            array.set_length(length)

        report_array(v["bitFlags"], bit_flags_type, "BitFlags", limb_count)
        report_array(v["keyFrames"], KeyFrameArrayResource, "KeyFrames", num_key_frames)
        report_array(v["kfNums"], KeyFrameS16ArrayResource, "KfNums", num_kf_nums)
        report_array(
            v["fixedValues"],
            KeyFrameS16ArrayResource,
            "FixedValues",
            num_fixed_values,
        )

    cdata_ext = CDataExt_Struct(
        (
            ("bitFlags", CDataExt_Value("I").set_write(write_segmented_pointer)),
            ("keyFrames", CDataExt_Value("I").set_write(write_segmented_pointer)),
            ("kfNums", CDataExt_Value("I").set_write(write_segmented_pointer)),
            ("fixedValues", CDataExt_Value("I").set_write(write_segmented_pointer)),
            ("unk_10", CDataExt_Value.s16),
            ("frameCount", CDataExt_Value.s16),
        )
    ).set_report(report_animation)

    def __init__(
        self,
        file: File,
        range_start: int,
        name: str,
        skeleton_offset: int,
        is_flex: bool,
    ):
        super().__init__(file, range_start, name)
        self.skeleton_offset = skeleton_offset
        self.is_flex = is_flex
        self.limb_count: int | None = None

    def try_parse_data(self, memory_context):
        if self.limb_count is None:
            assert self.file.data is not None
            # KeyFrameSkeleton.limbCount
            self.limb_count = self.file.data[self.skeleton_offset]
        return super().try_parse_data(memory_context)

    def write_extracted(self, memory_context):
        with self.extract_to_path.open("w") as f:
            if not self.braces_in_source:
                f.write("{\n")
            v = self.cdata_unpacked
            f.write("    { ")
            if v["bitFlags"] == 0:
                f.write("NULL")
            else:
                f.write(memory_context.get_c_reference_at_segmented(v["bitFlags"]))
            f.write(" }, // bitFlags\n")
            for member in ("keyFrames", "kfNums", "fixedValues"):
                f.write("    ")
                if v[member] == 0:
                    f.write("NULL")
                else:
                    f.write(memory_context.get_c_reference_at_segmented(v[member]))
                f.write(f", // {member}\n")
            f.write(f"    {v['unk_10']}, // unk_10\n")
            f.write(f"    {v['frameCount']}, // frameCount\n")
            if not self.braces_in_source:
                f.write("}\n")

    def get_c_declaration_base(self):
        return f"KeyFrameAnimation {self.symbol_name}"

    def get_c_reference(self, resource_offset: int):
        if resource_offset == 0:
            return f"&{self.symbol_name}"
        raise ValueError

    def get_h_includes(self):
        return (Z64HDRPRFX + "keyframe.h",)
