# Green Atlas: LibreDWG 0.14 MATERIAL ASCII export

Update: an additional opt-in patch stack is available after the user's explicit
request to continue CAD patches. See `2026-09-15-indexed-cad-patches.md` in
`docs/implementation`. The MATERIAL-only observations below remain historical.

`build-libredwg-material.ps1 -IncludeIndexedAcis` applies, in order:

1. `0.14-material-ascii-export.patch` — existing MATERIAL writer dispatch.
2. `0.14-indexed-acds-ascii.patch` — active datidx/64-bit search counts, inline
   SAB bound to source handles, unchanged bytes in standard ACDSDATA; reject
   missing modeler payloads before writing a document. No positional fallback.
3. `0.14-utf8-text-chunks.patch` — preserve UTF-8 characters across text groups.
4. `0.14-win-getopt-state.patch` — retain state in the bundled Windows getopt.

The patch stack reproduces the tested source from the pinned revision. No
runtime installation is performed. Paged SAB (`blob01`) remains explicitly
unsupported by the indexed patch. ACIS curve interpretation is outside it.
The ACDSDATA schema reuses ezdxf's standard schema; retain
`THIRD_PARTY_NOTICES.md` (the recipe copies it to `COPYING.ezdxf` in the source).
Do not distribute the binary without the complete corresponding patched source.

The local CMake test target references the evidence harness by an absolute
workspace path. It is deliberately excluded from all distributable patches.

Private downstream patch, not a new CAD serializer. Based on upstream tag 0.14,
commit `d9468ae948b8f07a08efa756c19f8916052358c0` (GPL-3.0-or-later).
Retain upstream license/source obligations when distributing a patched build.

`0.14-material-ascii-export.patch` adds seven lines in `src/out_dxf.c`.
It dispatches MATERIAL to the existing `dwg_dxf_MATERIAL` function without
enabling DEBUG_CLASSES. It preserves minimal-mode behavior, does not change
DWG encoding, binary DXF export, or the experimental-class policy elsewhere.

## Build / rollback

Windows lab recipe: `scripts/cad-lab/build-libredwg-material.ps1`.
The recipe requires a fresh folder on the checkout drive, pins the source
commit, verifies/applies the patch, and produces a build manifest. Use
`-WithoutPatch` and a separate fresh directory for a baseline build.
The manual equivalent was executed successfully; the wrapper has syntax
validation but has not been rerun from a second fresh clone.

No application config, installed converter, user database, or source file is
changed by this recipe. Rollback is selecting the previous executable; the
existing runtime converter has not been replaced in this experiment.

On Linux use the same pinned source and patch, initialize submodules, and
configure/build with upstream CMake from within the source directory. Linux
build/runtime verification remains outstanding; Windows is not the target
deployment OS in the LCT brief.

## Scope of verification

Full Parkovaya root DWG: identical baseline/patched compiler configuration.
572,668 pre-existing ASCII DXF records unchanged; exactly three MATERIAL
records added at original handles 11, 19, 21. Native-source comparison passed
163 checks on the three system materials. Canonical material tags survive
ezdxf save/read. Custom and advanced material variants remain untested.

**Not a complete lossless conversion:** existing REGION/ACIS issues remain;
ezdxf save/read drops four REGION with unavailable payload in this source.
No blanket audit-fix or material replacement is performed.

The tested MSVC build lacks iconv and has an upstream getopt implementation
which resets optind on each call, looping with options. Tested conversions use
positional-only input and ASCII-named copies on D. This lab executable is not
a drop-in runtime replacement. No unrelated getopt patch was added.
Upstream CMake embedded the parent-repo version when configured from the repo
root; the actual source revision is recorded by hash. The build recipe uses
the source working directory to avoid that metadata issue.

Reports: `docs/implementation/2026-09-15-official-implementation/cad/libredwg-material-patch/`.
