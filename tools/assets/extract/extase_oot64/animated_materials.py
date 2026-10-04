# SPDX-FileCopyrightText: © 2025 ZeldaRET
# SPDX-License-Identifier: CC0-1.0

import enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..extase.memorymap import MemoryContext

from ..extase import (
    File,
    ResourceParseWaiting,
)
from ..extase.memorymap import UnmappedAddressError
from ..extase.cdata_resources import (
    CDataResource,
    CDataArrayResource,
    CDataExt_Struct,
    CDataExt_Array,
    CDataExt_Value,
    CDataExtWriteContext,
)

Z64HDRPRFX = "z64"


class AnimatedMatType(enum.IntEnum):
    TEX_SCROLL = 0
    TWO_TEX_SCROLL = 1
    COLOR = 2
    COLOR_LERP = 3
    COLOR_NON_LINEAR_INTERP = 4
    TEX_CYCLE = 5
    # Not handled by the code (out of bounds of the draw handlers array),
    # but appears in data when the material list is empty.
    NONE = 6


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


def _try_read_segmented(memory_context: "MemoryContext", address: int, size: int):
    """Returns the bytes at a segmented address, or None if unmapped"""
    try:
        resolve_result = memory_context.resolve_segmented(address)
    except UnmappedAddressError:
        return None
    data = resolve_result.file.data
    if data is None:
        return None
    start = resolve_result.file_offset
    if start + size > len(data):
        return None
    return data[start : start + size]


#
# Type 0, 1: AnimatedMatTexScrollParams[1 or 2]
#


class AnimatedMatTexScrollParamsResource(CDataArrayResource):
    elem_cdata_ext = CDataExt_Struct(
        (
            ("xStep", CDataExt_Value.s8),
            ("yStep", CDataExt_Value.s8),
            ("width", CDataExt_Value.u8),
            ("height", CDataExt_Value.u8),
        )
    )

    def write_extracted(self, memory_context):
        with self.extract_to_path.open("w") as f:
            if not self.braces_in_source:
                f.write("{\n")
            for v in self.cdata_unpacked:
                f.write(
                    f"    {{ {v['xStep']}, {v['yStep']}, {v['width']}, {v['height']} }},\n"
                )
            if not self.braces_in_source:
                f.write("}\n")

    def get_c_declaration_base(self):
        return f"AnimatedMatTexScrollParams {self.symbol_name}[]"

    def get_h_includes(self):
        return (Z64HDRPRFX + "scene.h",)


#
# Type 2, 3, 4: AnimatedMatColorParams
#


class F3DPrimColorArrayResource(CDataArrayResource):
    elem_cdata_ext = CDataExt_Struct(
        (
            ("r", CDataExt_Value.u8),
            ("g", CDataExt_Value.u8),
            ("b", CDataExt_Value.u8),
            ("a", CDataExt_Value.u8),
            ("lodFrac", CDataExt_Value.u8),
        )
    )

    def write_extracted(self, memory_context):
        with self.extract_to_path.open("w") as f:
            if not self.braces_in_source:
                f.write("{\n")
            for v in self.cdata_unpacked:
                f.write(
                    f"    {{ {v['r']}, {v['g']}, {v['b']}, {v['a']}, {v['lodFrac']} }},\n"
                )
            if not self.braces_in_source:
                f.write("}\n")

    def get_c_declaration_base(self):
        return f"F3DPrimColor {self.symbol_name}[]"

    def get_h_includes(self):
        return (Z64HDRPRFX + "scene.h",)


class F3DEnvColorArrayResource(CDataArrayResource):
    elem_cdata_ext = CDataExt_Struct(
        (
            ("r", CDataExt_Value.u8),
            ("g", CDataExt_Value.u8),
            ("b", CDataExt_Value.u8),
            ("a", CDataExt_Value.u8),
        )
    )

    def write_extracted(self, memory_context):
        with self.extract_to_path.open("w") as f:
            if not self.braces_in_source:
                f.write("{\n")
            for v in self.cdata_unpacked:
                f.write(f"    {{ {v['r']}, {v['g']}, {v['b']}, {v['a']} }},\n")
            if not self.braces_in_source:
                f.write("}\n")

    def get_c_declaration_base(self):
        return f"F3DEnvColor {self.symbol_name}[]"

    def get_h_includes(self):
        return (Z64HDRPRFX + "scene.h",)


class U16ArrayResource(CDataArrayResource):
    elem_cdata_ext = CDataExt_Value.u16

    def write_extracted(self, memory_context):
        with self.extract_to_path.open("w") as f:
            if not self.braces_in_source:
                f.write("{\n")
            f.write("    ")
            f.write(", ".join(str(v) for v in self.cdata_unpacked))
            f.write(",\n")
            if not self.braces_in_source:
                f.write("}\n")

    def get_c_declaration_base(self):
        return f"u16 {self.symbol_name}[]"

    def get_h_includes(self):
        return ("ultra64.h",)


class U8ArrayResource(CDataArrayResource):
    elem_cdata_ext = CDataExt_Value.u8

    def write_extracted(self, memory_context):
        with self.extract_to_path.open("w") as f:
            if not self.braces_in_source:
                f.write("{\n")
            f.write("    ")
            f.write(", ".join(str(v) for v in self.cdata_unpacked))
            f.write(",\n")
            if not self.braces_in_source:
                f.write("}\n")

    def get_c_declaration_base(self):
        return f"u8 {self.symbol_name}[]"

    def get_h_includes(self):
        return ("ultra64.h",)


def _report_array(
    reporter,
    memory_context: "MemoryContext",
    address: int,
    resource_type: type[CDataArrayResource],
    name_suffix: str,
    length: int,
):
    if address == 0 or length <= 0:
        return
    resource = memory_context.report_resource_at_segmented(
        reporter,
        address,
        resource_type,
        lambda file, offset: resource_type(
            file, offset, f"{reporter.name}_{address:08X}_{name_suffix}"
        ),
    )
    if resource._length is None:
        resource.set_length(length)
    elif resource._length < length:
        # Shared by several params with different lengths, keep the longest.
        # This can only happen before the resource is parsed.
        assert not resource.is_data_parsed, resource
        resource._length = length


class AnimatedMatColorParamsResource(CDataResource):
    def report_params(resource, memory_context: "MemoryContext", v):
        assert isinstance(v, dict)
        if resource.mat_type == AnimatedMatType.COLOR:
            # no interpolation: indexed by the current frame directly
            num_colors = v["keyFrameLength"]
        else:
            num_colors = v["keyFrameCount"]
        _report_array(
            resource,
            memory_context,
            v["primColors"],
            F3DPrimColorArrayResource,
            "PrimColors",
            num_colors,
        )
        _report_array(
            resource,
            memory_context,
            v["envColors"],
            F3DEnvColorArrayResource,
            "EnvColors",
            num_colors,
        )
        _report_array(
            resource,
            memory_context,
            v["keyFrames"],
            U16ArrayResource,
            "KeyFrames",
            v["keyFrameCount"],
        )

    cdata_ext = CDataExt_Struct(
        (
            ("keyFrameLength", CDataExt_Value.u16),
            ("keyFrameCount", CDataExt_Value.u16),
            ("primColors", CDataExt_Value("I").set_write(write_segmented_pointer)),
            ("envColors", CDataExt_Value("I").set_write(write_segmented_pointer)),
            ("keyFrames", CDataExt_Value("I").set_write(write_segmented_pointer)),
        )
    ).set_report(report_params)

    def __init__(self, file: File, range_start: int, name: str, mat_type: int):
        super().__init__(file, range_start, name)
        self.mat_type = mat_type

    def get_c_declaration_base(self):
        return f"AnimatedMatColorParams {self.symbol_name}"

    def get_c_reference(self, resource_offset: int):
        if resource_offset == 0:
            return f"&{self.symbol_name}"
        raise ValueError

    def get_h_includes(self):
        return (Z64HDRPRFX + "scene.h",)


#
# Type 5: AnimatedMatTexCycleParams
#


class TexturePtrArrayResource(CDataArrayResource):
    elem_cdata_ext = CDataExt_Value("I").set_write(write_segmented_pointer)

    def get_c_declaration_base(self):
        return f"TexturePtr {self.symbol_name}[]"

    def get_h_includes(self):
        return ("ultra64.h",)


class AnimatedMatTexCycleParamsResource(CDataResource):
    def report_params(resource, memory_context: "MemoryContext", v):
        assert isinstance(v, dict)
        keyFrameLength = v["keyFrameLength"]
        textureIndexList = v["textureIndexList"]
        _report_array(
            resource,
            memory_context,
            textureIndexList,
            U8ArrayResource,
            "TextureIndexList",
            keyFrameLength,
        )
        index_data = _try_read_segmented(
            memory_context, textureIndexList, keyFrameLength
        )
        if index_data is not None:
            _report_array(
                resource,
                memory_context,
                v["textureList"],
                TexturePtrArrayResource,
                "TextureList",
                max(index_data) + 1,
            )

    cdata_ext = CDataExt_Struct(
        (
            ("keyFrameLength", CDataExt_Value.u16),
            ("pad2", CDataExt_Value.pad16),
            ("textureList", CDataExt_Value("I").set_write(write_segmented_pointer)),
            (
                "textureIndexList",
                CDataExt_Value("I").set_write(write_segmented_pointer),
            ),
        )
    ).set_report(report_params)

    def get_c_declaration_base(self):
        return f"AnimatedMatTexCycleParams {self.symbol_name}"

    def get_c_reference(self, resource_offset: int):
        if resource_offset == 0:
            return f"&{self.symbol_name}"
        raise ValueError

    def get_h_includes(self):
        return (Z64HDRPRFX + "scene.h",)


#
# AnimatedMaterial[]
#


ANIMATED_MAT_TYPE_NAMES = {
    AnimatedMatType.TEX_SCROLL: "ANIM_MAT_TYPE_TEX_SCROLL",
    AnimatedMatType.TWO_TEX_SCROLL: "ANIM_MAT_TYPE_TWO_TEX_SCROLL",
    AnimatedMatType.COLOR: "ANIM_MAT_TYPE_COLOR",
    AnimatedMatType.COLOR_LERP: "ANIM_MAT_TYPE_COLOR_LERP",
    AnimatedMatType.COLOR_NON_LINEAR_INTERP: "ANIM_MAT_TYPE_COLOR_NON_LINEAR_INTERP",
    AnimatedMatType.TEX_CYCLE: "ANIM_MAT_TYPE_TEX_CYCLE",
    AnimatedMatType.NONE: "ANIM_MAT_TYPE_NONE",
}


class AnimatedMaterialResource(CDataArrayResource):
    def report_elem(resource, memory_context: "MemoryContext", v):
        assert isinstance(v, dict)
        segment = v["segment"]
        mat_type = v["type"]
        address = v["params"]
        if address == 0 or (segment == 0 and resource._length == 1):
            # Empty list (not drawn), params is meaningless
            return
        if mat_type in {AnimatedMatType.TEX_SCROLL, AnimatedMatType.TWO_TEX_SCROLL}:
            _report_array(
                resource,
                memory_context,
                address,
                AnimatedMatTexScrollParamsResource,
                "TexScrollParams",
                1 if mat_type == AnimatedMatType.TEX_SCROLL else 2,
            )
        elif mat_type in {
            AnimatedMatType.COLOR,
            AnimatedMatType.COLOR_LERP,
            AnimatedMatType.COLOR_NON_LINEAR_INTERP,
        }:
            memory_context.report_resource_at_segmented(
                resource,
                address,
                AnimatedMatColorParamsResource,
                lambda file, offset: AnimatedMatColorParamsResource(
                    file,
                    offset,
                    f"{resource.name}_{address:08X}_ColorParams",
                    mat_type,
                ),
            )
        elif mat_type == AnimatedMatType.TEX_CYCLE:
            memory_context.report_resource_at_segmented(
                resource,
                address,
                AnimatedMatTexCycleParamsResource,
                lambda file, offset: AnimatedMatTexCycleParamsResource(
                    file, offset, f"{resource.name}_{address:08X}_TexCycleParams"
                ),
            )
        else:
            raise NotImplementedError("AnimatedMaterial type", mat_type, resource)

    def write_elem(
        resource, memory_context: "MemoryContext", v, wctx: CDataExtWriteContext
    ):
        assert isinstance(v, dict)
        segment = v["segment"]
        mat_type = v["type"]
        address = v["params"]
        f = wctx.f
        f.write(wctx.line_prefix)
        if segment > 0 or (segment == 0 and resource._length != 1):
            segment_str = f"MATERIAL_SEGMENT_NUM(0x{segment + 7:X})"
        elif segment < 0:
            segment_str = f"LAST_MATERIAL_SEGMENT_NUM(0x{-segment + 7:X})"
        else:
            segment_str = "0"
        type_str = ANIMATED_MAT_TYPE_NAMES.get(mat_type, str(mat_type))
        if address == 0:
            params_str = "NULL"
        elif segment == 0 and resource._length == 1:
            params_str = f"(void*){address:#010X}"
        else:
            params_str = memory_context.get_c_reference_at_segmented(address)
        f.write(f"{{ {segment_str}, {type_str}, {params_str} }}")
        return True

    elem_cdata_ext = (
        CDataExt_Struct(
            (
                ("segment", CDataExt_Value.s8),
                ("pad1", CDataExt_Value.pad8),
                ("type", CDataExt_Value.s16),
                ("params", CDataExt_Value("I")),
            )
        )
        .set_report(report_elem)
        .set_write(write_elem)
    )

    def try_parse_data(self, memory_context):
        if self._length is None:
            assert self.file.data is not None
            length = 0
            while True:
                v = self.elem_cdata_ext.unpack_from(
                    self.file.data,
                    self.range_start + length * self.elem_cdata_ext.size,
                )
                length += 1
                # The draw code does not draw anything if the first segment is 0,
                # otherwise it stops after the first negative segment.
                if (length == 1 and v["segment"] == 0) or v["segment"] < 0:
                    break
            self.set_length(length)
        return super().try_parse_data(memory_context)

    def get_c_declaration_base(self):
        if hasattr(self, "HACK_IS_STATIC_ON"):
            return f"AnimatedMaterial {self.symbol_name}[{self._length}]"
        return f"AnimatedMaterial {self.symbol_name}[]"

    def get_h_includes(self):
        return ("ultra64.h", Z64HDRPRFX + "scene.h")
