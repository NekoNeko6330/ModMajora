# SPDX-FileCopyrightText: © 2025 ZeldaRET
# SPDX-License-Identifier: CC0-1.0

"""Parse a preprocessed spec file (build/VERSION/spec)."""

import dataclasses
from pathlib import Path


@dataclasses.dataclass
class Segment:
    name: str
    includes: list[str] = dataclasses.field(default_factory=list)
    flags: list[str] = dataclasses.field(default_factory=list)
    compress: bool = False
    address: int | None = None


def parse_spec(path: Path, build_dir: str) -> list[Segment]:
    """Parse the segments of a preprocessed spec.

    Include paths have $(BUILD_DIR) replaced with build_dir.
    """
    segments: list[Segment] = []
    cur: Segment | None = None
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("/*") or line.startswith("*"):
            continue
        parts = line.split(None, 1)
        kw = parts[0]
        arg = parts[1] if len(parts) > 1 else ""
        if kw == "beginseg":
            cur = Segment("")
        elif kw == "endseg":
            assert cur is not None
            segments.append(cur)
            cur = None
        elif cur is None:
            continue
        elif kw == "name":
            cur.name = arg.strip('"')
        elif kw == "include":
            cur.includes.append(arg.strip('"').replace("$(BUILD_DIR)", build_dir))
        elif kw == "flags":
            cur.flags.extend(arg.split())
        elif kw == "compress":
            cur.compress = True
        elif kw == "address":
            cur.address = int(arg, 0)
    return segments
