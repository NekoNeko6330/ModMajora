Assets are not committed to the repo; instead, they are extracted from the ROM files as part of `make assets` (or `make init`).

Assets are extracted to `extracted/VERSION/assets` (for example `extracted/n64-us/assets` for the `n64-us` version), based on the descriptions stored in xml files in `assets/xml/`.
Note that for now, assets are always extracted from the `n64-us` baserom, even when building other versions.

For details on the xml files contents, see [the assets xml specification file](../../tools/assets/descriptor/spec.md).

The extraction tool can use [rich](https://github.com/Textualize/rich) if installed to make output prettier.
If you are looking at output or errors from during extraction, consider installing rich for a better experience: `.venv/bin/python3 -m pip install rich`

To run the extraction outside of `make assets`, use `./tools/extract_assets.sh VERSION`.
- Pass `-f` to force extraction: otherwise only assets for which xmls were modified will be extracted.
- Pass `-j` to use multiprocessing, making extraction quicker. Note that this makes for less readable errors if any error happens.
- Pass `-s name` to extract assets using baserom file `name`.
- Pass `-r -s 'name.*'` to extract assets using baserom files whose name match regular expression `name.*`.

Each extracted asset file `extracted/VERSION/assets/path/to/name.c` is a "source" file defining the symbols, whose data is included from `.inc.c` files.
Those `.inc.c` files are either also extracted (for example the C representation of a display list) or written by the build system (for example from `.png` files, see [images](images.md)).

Cutscene scripts are disassembled using [`tools/csdis.py`](../../tools/csdis.py), which can also be used standalone to disassemble a cutscene from any file.

Data that is not described in the xmls and not referenced by other extracted data is extracted as `unaccounted` binary blobs.

There currently are various hacks in place in the extraction tool source code to make extraction of some corner cases possible, or to silence extraction warnings.
Some of these hacks check for the name of resources, so renaming a few specific resources may need updating the extraction tool's source too.
The plan is to eventually remove those hardcoded checks from the source and use a `HackMode` attribute in the xmls to trigger the hacks code paths.
