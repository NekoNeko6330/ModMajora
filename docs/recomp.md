# Exporting a Majora's Mask: Recompiled mod (`make nrm`)

`make nrm` packages every change made to the game in this repository as a mod for
[Majora's Mask: Recompiled](https://github.com/Zelda64Recomp/Zelda64Recomp), in its `.nrm` format
(the same format as mods built from the
[MMRecompModTemplate](https://github.com/Zelda64Recomp/MMRecompModTemplate)).

You keep modding the decompilation as usual: edit code, actors, scenes, objects, textures, text,
music, etc. The exporter then works out the difference between your tree and the vanilla game and
turns it into a mod. The mod is self-contained: it doesn't depend on any other mod.

```sh
make nrm
# -> build/modmajora.nrm, to put in the recomp's mods folder
```

The first run takes a while (see [The vanilla base](#the-vanilla-base)), later runs are incremental.

## Requirements

- The usual build requirements, with a successful `make setup && make assets && make disasm`
- `mips-linux-gnu-gcc` (the exporter compiles the code with GCC)
- `ld.lld` (LLVM linker)
- `cmake`, `ninja` (optional) and a C++ compiler, to build the
  [N64Recomp](https://github.com/N64Recomp/N64Recomp) tools the first time. Prebuilt `N64Recomp` and
  `RecompModTool` executables can be put in `tools/N64Recomp/bin/` instead.

Only the US version (`n64-us`) is supported, as it is the version Majora's Mask: Recompiled runs.

## Configuration: `recomp/nrm.toml`

| Key | Meaning |
| --- | --- |
| `base_commit` | Commit of this repository corresponding to the vanilla game (see below) |
| `mod_filename` | Name of the `.nrm` file |
| `thumbnail` | Optional PNG shown in the recomp's mod menu |
| `allow_force_patch` | Allow replacing functions the recomp already patches (see [Conflicts](#conflicts-with-the-recomp)) |
| `[features]` | Feature overrides for the recomp build (see [N64 vs recomp code](#n64-vs-recomp-code)) |
| `[manifest]` | The mod's manifest: `id`, `version`, `display_name`, `description`, `short_description`, `authors`, `minimum_recomp_version`, `dependencies`, `config_options`, ... as in the template's `mod.toml` |

## How it works

Majora's Mask: Recompiled doesn't run a ROM's code: it runs the vanilla game's code, recompiled
ahead of time. A recomp mod therefore can't just be a modified ROM. It is made of:

- **Code**: functions replacing vanilla functions (patches) and new functions, recompiled when the
  mod is loaded
- **Data**: a ROM patch (`patch.bps` inside the `.nrm`), applied to the vanilla ROM

The exporter (`tools/nrm`) builds both from your tree:

1. **Vanilla base**: the `base_commit` is checked out in a git worktree and built to match the
   vanilla ROM, with debug information. N64Recomp generates the recomp symbol files (names and
   addresses of all vanilla functions and data) from it.
2. **Build**: your tree is built for the recomp target (`make TARGET=recomp`, in
   `build/n64-us-recomp`). The ROM built this way provides the data files.
3. **ROM layout**: data files (scenes, rooms, objects, textures, audio, text, ...) that changed are
   written to a copy of the vanilla ROM: in place when they fit, otherwise at the end of the ROM.
   New files are added at the end. Code files keep their vanilla contents and addresses, as the
   recomp finds overlays by their vanilla ROM address. `dmadata` (the file table) is updated.
4. **Code diff**: the code of the base and of your tree is compiled with the same flags, one section
   per function and per variable, and compared symbol by symbol (relocations are compared
   symbolically). This selects:
   - changed functions: they become patches (`RECOMP_PATCH`)
   - new functions: added to the mod
   - functions referencing something that moved (e.g. an asset of an object that changed size, or a
     file that moved in the ROM): patched, with the new address
   - changed data: if it kept its size, and only points to vanilla addresses, it is patched in
     place in the ROM (the recomp loads code segments' data from the ROM). Otherwise it is copied
     into the mod, and every function and data using it is patched to use the copy.
   - anything the above needs that doesn't exist in the vanilla game (string literals, constants,
     GCC-generated helpers, ...)
5. **New actors**: new actor overlays become internal actors: their code lives in the mod, and their
   entry in the actor overlay table has no overlay (`vramStart == NULL`), so the game uses their
   profile directly.
6. **Packaging**: the selected code is written to a relocatable object, linked, and packaged with
   `RecompModTool` along with `patch.bps`.

The report in `build/recomp/mod/report.txt` lists everything that went into the mod, and why.
`build/recomp/mod/` also holds the intermediate files (`mod.elf`, `mod.toml`, `datasyms.toml`, ...).

### The vanilla base

`base_commit` must be a commit of this repository whose matching build (`make COMPILER=ido
NON_MATCHING=0 COMPARE=1`) produces the vanilla ROM. Everything that differs between that commit and
your tree is exported, so it should be the last commit before you started modding, or any later
commit that only contains decompilation work (renames, documentation, matched functions, ...).
Moving it forward when pulling new decompilation work keeps the diff small.

The base is prepared once per commit, in `build/recomp/base/<commit>/`.

## N64 vs recomp code

The recomp adds its own features (widescreen, camera, input, framerate, ...) by patching vanilla
functions. A change you make for the N64 version may conflict with them, e.g. a custom camera.

`include/modmajora_config.h` defines the build target and features, so code can differ between the
N64 build (`make`, `TARGET=n64`) and the recomp mod (`make nrm`, `TARGET=recomp`):

```c
#include "modmajora_config.h" // already included by most files, through versions.h

#if TARGET_N64
// N64 only
#endif

#if TARGET_RECOMP
// recomp only
#endif
```

For features that should be configurable, declare them in `modmajora_config.h`, with a default for
each target:

```c
#ifdef FEATURE_CUSTOM_CAMERA_OVERRIDE
#define FEATURE_CUSTOM_CAMERA FEATURE_CUSTOM_CAMERA_OVERRIDE
#else
#define FEATURE_CUSTOM_CAMERA FEATURE_DEFAULT(1, 0) // on for N64, off for recomp
#endif
```

and use them with `#if FEATURE(CUSTOM_CAMERA)`. The defaults can be overridden with
`make FEATURE_CUSTOM_CAMERA=1` (N64 build), `make nrm FEATURE_CUSTOM_CAMERA=1`, or in the
`[features]` table of `recomp/nrm.toml`.

### Recomp-only mod code: `recomp/src/`

C files in `recomp/src/` are compiled into the mod only. They can use the recomp mod API
(`include/recomp/`): hooks, callbacks, imports and exports, config options, the UI, ...

```c
#include "recomp/modding.h"
#include "z64.h"

RECOMP_HOOK("Player_Init") void MyMod_OnPlayerInit(Actor* thisx, PlayState* play) {
    // runs before Player_Init
}
```

A `RECOMP_PATCH` or `RECOMP_FORCE_PATCH` function in `recomp/src/` replaces the tree's version of
that function in the mod.

## Conflicts with the recomp

Majora's Mask: Recompiled patches some vanilla functions itself (listed in
`tools/nrm/recomp_patched_functions.txt`). A mod can only replace them with `RECOMP_FORCE_PATCH`,
which discards the recomp's version (and the features it implements).

- If one of your changes requires patching such a function, `make nrm` stops and lists them.
  Either keep the change out of the recomp build (`#if TARGET_N64`, or a feature), or set
  `allow_force_patch = true` to replace them anyway.
- If such a function only needs patching because it uses data that grew (e.g. the object table,
  when adding an object), it keeps using the vanilla data instead, with its original entries
  updated in place. `make nrm` prints a warning. With `allow_force_patch = true`, it is replaced
  instead.

## Limitations

- Mods with a `patch.bps` can't be combined with other mods that also have one.
- New code segments must be actor overlays (they become internal actors). Other new overlays
  (effects, game states, ...) are not supported.
- Data files can't point to new code (e.g. a scene pointing to a function of a new actor).
- Changed data inside an actor overlay that is copied into the mod (because it grew, or points to
  new data) can't point to other symbols of that overlay; `RecompModTool` reports it. Keeping the
  data's size, or moving the change to the code, avoids it.
- Recomp mod code is compiled with GCC, without the decompilation's matching constraints: behavior
  relying on IDO-specific code generation may differ.
