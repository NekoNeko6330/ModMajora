# SPDX-FileCopyrightText: © 2025 ZeldaRET
# SPDX-License-Identifier: CC0-1.0

"""Resources for MM-specific scene data:
actor cutscenes, cutscene scripts, map data, light lists"""

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
    CDataArrayNamedLengthResource,
    CDataExt_Struct,
    CDataExt_Array,
    CDataExt_Value,
    CDataExtWriteContext,
    Vec3sArrayResource,
)

from .. import oot64_data
from . import misc_resources
from . import collision_resources

Z64HDRPRFX = "z64"


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
# Actor cutscenes (CutsceneEntry[])
#

CS_CAM_ID_NAMES = {
    -25: "CS_CAM_ID_GLOBAL_ELEGY",
    -24: "CS_CAM_ID_GLOBAL_SIDED",
    -23: "CS_CAM_ID_GLOBAL_BOAT_CRUISE",
    -22: "CS_CAM_ID_GLOBAL_N16",
    -21: "CS_CAM_ID_GLOBAL_SUBJECTD",
    -20: "CS_CAM_ID_GLOBAL_NORMALD",
    -19: "CS_CAM_ID_GLOBAL_N13",
    -18: "CS_CAM_ID_GLOBAL_N12",
    -17: "CS_CAM_ID_GLOBAL_N11",
    -16: "CS_CAM_ID_GLOBAL_WARP_PAD_ENTRANCE",
    -15: "CS_CAM_ID_GLOBAL_ATTENTION",
    -14: "CS_CAM_ID_GLOBAL_CONNECT",
    -13: "CS_CAM_ID_GLOBAL_REMOTE_BOMB",
    -12: "CS_CAM_ID_GLOBAL_N0C",
    -11: "CS_CAM_ID_GLOBAL_MASK_TRANSFORMATION",
    -10: "CS_CAM_ID_GLOBAL_LONG_CHEST_OPENING",
    -9: "CS_CAM_ID_GLOBAL_REVIVE",
    -8: "CS_CAM_ID_GLOBAL_DEATH",
    -7: "CS_CAM_ID_GLOBAL_WARP_PAD_MOON",
    -6: "CS_CAM_ID_GLOBAL_SONG_WARP",
    -5: "CS_CAM_ID_GLOBAL_ITEM_SHOW",
    -4: "CS_CAM_ID_GLOBAL_ITEM_BOTTLE",
    -3: "CS_CAM_ID_GLOBAL_ITEM_OCARINA",
    -2: "CS_CAM_ID_GLOBAL_ITEM_GET",
    -1: "CS_CAM_ID_NONE",
}

CS_ID_NAMES = {
    -1: "CS_ID_NONE",
    0x78: "CS_ID_GLOBAL_78",
    0x79: "CS_ID_GLOBAL_79",
    0x7A: "CS_ID_GLOBAL_7A",
    0x7B: "CS_ID_GLOBAL_ELEGY",
    0x7C: "CS_ID_GLOBAL_TALK",
    0x7D: "CS_ID_GLOBAL_DOOR",
    0x7E: "CS_ID_GLOBAL_RETURN_TO_CAM",
    0x7F: "CS_ID_GLOBAL_END",
}

CS_HUD_VISIBILITY_NAMES = {
    -1: "CS_HUD_VISIBILITY_ALL_ALT",
    0: "CS_HUD_VISIBILITY_NONE",
    1: "CS_HUD_VISIBILITY_ALL",
    2: "CS_HUD_VISIBILITY_A_HEARTS_MAGIC",
    3: "CS_HUD_VISIBILITY_C_HEARTS_MAGIC",
    4: "CS_HUD_VISIBILITY_ALL_NO_MINIMAP",
    5: "CS_HUD_VISIBILITY_A_B_C",
    6: "CS_HUD_VISIBILITY_B_MINIMAP",
    7: "CS_HUD_VISIBILITY_A",
}

CS_END_SFX_NAMES = {
    0: "CS_END_SFX_NONE",
    1: "CS_END_SFX_TRE_BOX_APPEAR",
    2: "CS_END_SFX_CORRECT_CHIME",
    0xFF: "CS_END_SFX_NONE_ALT",
}

CS_END_CAM_NAMES = {
    0: "CS_END_CAM_0",
    1: "CS_END_CAM_1",
    2: "CS_END_CAM_SMOOTH",
}


def _name_or_int(names: dict[int, str]):
    return lambda v: names.get(v, str(v))


class ActorCutsceneListResource(CDataArrayNamedLengthResource):
    elem_cdata_ext = CDataExt_Struct(
        (
            ("priority", CDataExt_Value.s16),
            ("length", CDataExt_Value.s16),
            (
                "csCamId",
                CDataExt_Value("h").set_write_str_v(_name_or_int(CS_CAM_ID_NAMES)),
            ),
            (
                "scriptIndex",
                CDataExt_Value("h").set_write_str_v(
                    lambda v: "CS_SCRIPT_ID_NONE" if v == -1 else str(v)
                ),
            ),
            (
                "additionalCsId",
                CDataExt_Value("h").set_write_str_v(_name_or_int(CS_ID_NAMES)),
            ),
            (
                "endSfx",
                CDataExt_Value("B").set_write_str_v(_name_or_int(CS_END_SFX_NAMES)),
            ),
            ("customValue", CDataExt_Value.u8),
            (
                "hudVisibility",
                CDataExt_Value("h").set_write_str_v(
                    _name_or_int(CS_HUD_VISIBILITY_NAMES)
                ),
            ),
            (
                "endCam",
                CDataExt_Value("B").set_write_str_v(_name_or_int(CS_END_CAM_NAMES)),
            ),
            ("letterboxSize", CDataExt_Value.u8),
        )
    )

    def get_c_declaration_base(self):
        return f"CutsceneEntry {self.symbol_name}[{self.length_name}]"

    def get_h_includes(self):
        return (Z64HDRPRFX + "cutscene.h",)


#
# Actor cutscene cameras (ActorCsCamInfo[])
#


class ActorCsCamInfoListResource(CDataArrayNamedLengthResource):
    def write_elem(
        resource, memory_context: "MemoryContext", v, wctx: CDataExtWriteContext
    ):
        assert isinstance(v, dict)
        address = v["actorCsCamFuncData"]
        f = wctx.f
        f.write(wctx.line_prefix)
        f.write("{ ")
        f.write(oot64_data.get_camera_setting_type_name(v["setting"]))
        f.write(f", {v['count']}, ")
        if address == 0:
            f.write("NULL")
        else:
            f.write(memory_context.get_c_reference_at_segmented(address))
        f.write(" }")
        return True

    elem_cdata_ext = CDataExt_Struct(
        (
            ("setting", CDataExt_Value.s16),
            ("count", CDataExt_Value.s16),
            ("actorCsCamFuncData", CDataExt_Value("I")),  # Vec3s*
        )
    ).set_write(write_elem)

    def try_parse_data(self, memory_context):
        ret = super().try_parse_data(memory_context)
        if not getattr(self, "_is_cam_data_reported", False):
            # The camera data of all entries is in a single Vec3s array
            # (with no padding between the entries' data)
            ranges = [
                (v["actorCsCamFuncData"], v["count"])
                for v in self.cdata_unpacked
                if v["actorCsCamFuncData"] != 0 and v["count"] > 0
            ]
            if ranges:
                start = min(address for address, count in ranges)
                end = max(address + count * 6 for address, count in ranges)
                memory_context.report_resource_at_segmented(
                    self,
                    start,
                    collision_resources.BgCamFuncDataResource,
                    lambda file, offset: collision_resources.BgCamFuncDataResource(
                        file,
                        offset,
                        offset + end - start,
                        f"{self.name}_{start:08X}_CamData",
                    ),
                )
            self._is_cam_data_reported = True
        return ret

    def get_c_declaration_base(self):
        return f"ActorCsCamInfo {self.symbol_name}[{self.length_name}]"

    def get_c_includes(self):
        return (Z64HDRPRFX + "camera.h",)

    def get_h_includes(self):
        return (Z64HDRPRFX + "scene.h",)


#
# Cutscene scripts (CutsceneScriptEntry[])
#


def write_cs_spawn_flags(v: int):
    if v == 0xFF:
        return "CS_SPAWN_FLAG_NONE"
    if v == 0xFE:
        return "CS_SPAWN_FLAG_ALWAYS"
    index = v >> 3
    mask = 1 << (v & 7)
    return f"CS_SPAWN_FLAG_ONCE({oot64_data.get_weekeventreg_flag_name(index, mask)})"


class CutsceneScriptListResource(CDataArrayNamedLengthResource):
    def report_script(resource, memory_context: "MemoryContext", v):
        assert isinstance(v, int)
        address = v
        if address == 0:
            return
        memory_context.report_resource_at_segmented(
            resource,
            address,
            misc_resources.CutsceneResource,
            lambda file, offset: misc_resources.CutsceneResource(
                file, offset, f"{resource.name}_{address:08X}_CsData"
            ),
        )

    elem_cdata_ext = CDataExt_Struct(
        (
            (
                "script",
                CDataExt_Value("I")
                .set_report(report_script)
                .set_write(write_segmented_pointer),
            ),
            (
                "nextEntrance",
                CDataExt_Value("h").set_write_str_v(
                    lambda v: f"{v:#06X}" if v >= 0 else str(v)
                ),
            ),
            ("spawn", CDataExt_Value.u8),
            (
                "spawnFlags",
                CDataExt_Value("B").set_write_str_v(write_cs_spawn_flags),
            ),
        )
    )

    def get_c_declaration_base(self):
        return f"CutsceneScriptEntry {self.symbol_name}[{self.length_name}]"

    def get_c_includes(self):
        return (Z64HDRPRFX + "save.h",)  # for WEEKEVENTREG_*

    def get_h_includes(self):
        return (Z64HDRPRFX + "cutscene.h",)


#
# Map data (MapDataScene, MapDataRoom[], MapDataChest[])
#


def write_map_data_room_flags(v: int):
    flags = []
    if v & 1:
        flags.append("MAP_DATA_ROOM_FLIP_X")
    if v & 2:
        flags.append("MAP_DATA_ROOM_FLIP_Y")
    rest = v & ~3
    if rest:
        flags.append(f"{rest:#X}")
    return " | ".join(flags) if flags else "0"


class MapDataRoomListResource(CDataArrayResource):
    elem_cdata_ext = CDataExt_Struct(
        (
            (
                "mapId",
                CDataExt_Value("H").set_write_str_v(
                    lambda v: "MAP_DATA_NO_MAP" if v == 0xFFFF else f"{v:#X}"
                ),
            ),
            ("centerX", CDataExt_Value.s16),
            ("floorY", CDataExt_Value.s16),
            ("centerZ", CDataExt_Value.s16),
            (
                "flags",
                CDataExt_Value("H").set_write_str_v(write_map_data_room_flags),
            ),
        )
    )

    def get_c_declaration_base(self):
        return f"MapDataRoom {self.symbol_name}[]"

    def get_h_includes(self):
        return (Z64HDRPRFX + "scene.h",)


class MapDataSceneResource(CDataResource):
    def __init__(self, file: File, range_start: int, name: str):
        super().__init__(file, range_start, name)
        self.num_rooms: int | None = None

    def set_num_rooms(self, num_rooms: int):
        if self.num_rooms is not None and self.num_rooms != num_rooms:
            raise Exception("num_rooms already set", self, self.num_rooms, num_rooms)
        self.num_rooms = num_rooms

    def report_rooms(resource: "MapDataSceneResource", memory_context, v):
        assert isinstance(v, int)
        address = v
        if address == 0:
            return
        assert resource.num_rooms is not None
        rooms = memory_context.report_resource_at_segmented(
            resource,
            address,
            MapDataRoomListResource,
            lambda file, offset: MapDataRoomListResource(
                file, offset, f"{resource.name}_{address:08X}_Rooms"
            ),
        )
        rooms.set_length(resource.num_rooms)

    cdata_ext = CDataExt_Struct(
        (
            (
                "rooms",
                CDataExt_Value("I")
                .set_report(report_rooms)
                .set_write(write_segmented_pointer),
            ),
            ("scale", CDataExt_Value.s16),
            ("pad6", CDataExt_Value.pad16),
        )
    )

    def try_parse_data(self, memory_context):
        if self.num_rooms is None:
            raise ResourceParseWaiting(waiting_for=["self.num_rooms"])
        return super().try_parse_data(memory_context)

    def get_c_declaration_base(self):
        return f"MapDataScene {self.symbol_name}"

    def get_c_reference(self, resource_offset: int):
        if resource_offset == 0:
            return f"&{self.symbol_name}"
        raise ValueError

    def get_h_includes(self):
        return (Z64HDRPRFX + "scene.h",)


class MapDataChestListResource(CDataArrayNamedLengthResource):
    elem_cdata_ext = CDataExt_Struct(
        (
            ("room", CDataExt_Value.s16),
            ("chestFlagId", CDataExt_Value.s16),
            ("x", CDataExt_Value.s16),
            ("y", CDataExt_Value.s16),
            ("z", CDataExt_Value.s16),
        )
    )

    def write_extracted(self, memory_context):
        with self.extract_to_path.open("w") as f:
            if not self.braces_in_source:
                f.write("{\n")
            f.write("    // { room, chestFlagId, x, y, z }\n")
            for v in self.cdata_unpacked:
                f.write(
                    "    { "
                    + ", ".join(
                        f"{v[k]:6}" for k in ("room", "chestFlagId", "x", "y", "z")
                    )
                    + " },\n"
                )
            if not self.braces_in_source:
                f.write("}\n")

    def get_c_declaration_base(self):
        return f"MapDataChest {self.symbol_name}[{self.length_name}]"

    def get_h_includes(self):
        return (Z64HDRPRFX + "scene.h",)


#
# Lights (LightInfo[])
#

LIGHT_TYPE_NAMES = {
    0: "LIGHT_POINT_NOGLOW",
    1: "LIGHT_DIRECTIONAL",
    2: "LIGHT_POINT_GLOW",
}


class LightInfoListResource(CDataArrayNamedLengthResource):
    elem_cdata_ext = CDataExt_Struct(
        (
            ("type", CDataExt_Value.u8),
            ("pad1", CDataExt_Value.pad8),
            ("params", CDataExt_Array(CDataExt_Value.u8, 12)),
        )
    )

    def write_extracted(self, memory_context):
        import struct

        with self.extract_to_path.open("w") as f:
            if not self.braces_in_source:
                f.write("{\n")
            for v in self.cdata_unpacked:
                light_type = v["type"]
                params = bytes(v["params"])
                dir_x, dir_y, dir_z, dir_r, dir_g, dir_b = struct.unpack_from(
                    ">bbbBBB", params
                )
                x, y, z, r, g, b, drawGlow, radius = struct.unpack_from(
                    ">hhhBBBBh", params
                )
                if light_type == 1 and params[6:] == bytes(6):
                    f.write(
                        "    LIGHT_INFO_DIRECTIONAL("
                        f"{dir_x}, {dir_y}, {dir_z}, "
                        f"{dir_r}, {dir_g}, {dir_b}"
                        "),\n"
                    )
                elif light_type in {0, 2}:
                    drawGlow_str = (
                        ("true" if drawGlow else "false")
                        if drawGlow in {0, 1}
                        else str(drawGlow)
                    )
                    f.write(
                        "    LIGHT_INFO_POINT("
                        f"{LIGHT_TYPE_NAMES[light_type]}, "
                        f"{x}, {y}, {z}, "
                        f"{r}, {g}, {b}, "
                        f"{drawGlow_str}, {radius}"
                        "),\n"
                    )
                else:
                    raise NotImplementedError(
                        "Unexpected LightInfo", self, light_type, params
                    )
            if not self.braces_in_source:
                f.write("}\n")

    def get_c_declaration_base(self):
        return f"LightInfo {self.symbol_name}[{self.length_name}]"

    def get_c_includes(self):
        return ("stdbool.h",)

    def get_h_includes(self):
        return (Z64HDRPRFX + "light.h",)
