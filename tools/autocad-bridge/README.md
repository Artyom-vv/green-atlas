# Green Atlas AutoCAD bridge

## Current direction — 23 September

Read [the authoritative development contract](../../docs/architecture/autocad-contract.md)
and [pipeline mismatch register](../../docs/audits/2026-09-22-cad-pipeline.md) first.
The installed-button path and diagnostic live import are not yet unified.
Live export is not connected. The GAOPEN producer-version mismatch (C03) is
fixed in the **uninstalled** 0.1.34 test package; it is not evidence that the
currently installed button is ready. The same build adds a narrow native
two-polyline REGION path with strict source provenance. Real-street capture and
calculation acceptance remain pending; see
[the 23 September native report](../../docs/implementation/2026-09-23-native-multicurve-buildings.md).
Historical acceptance below does not qualify the present complete product.
Do not develop a parallel portable-parser import. Geometry-engine choice is pending.
Local dependency graphs, the legacy ratchet and native clangd setup:
[development tooling](../../scripts/architecture/README.md).

## Product bridge work in progress — 20 September

The current priority is a user-installable AutoCAD → local Green Atlas integration, not
further geometry expansion. Track it in
[the product bridge goal](../../docs/implementation/2026-09-20-autocad-product-bridge-goal.md).
The developer-oriented export/MCP workflow below is **not** the finished user flow.

**User decision: everything local.** The new package contains `Green Atlas.app`
(AppKit/WKWebView + embedded API runtime and web assets), not the historical
remote `Green Atlas Connect.app`. GAOPEN opens the bundled app in the background;
the app owns one runtime, queues native tickets and reveals the project. No URL
field, browser pairing, external browser or remote upload belongs to this flow.
Local handoff verifies all hashes, preserves source files and reuses a reserved
project ID on retry. Python, Node, SDK and the repository are build dependencies,
not user dependencies. Legacy `connector/` remains historical code, NOT a second
active product route; current builders do not package it.

The current complete package is **macOS 26, Apple Silicon, AutoCAD 2027 only**.
The older native bridge's universal binary/macOS14 target does not make its new
embedded Python runtime compatible with older systems. Windows is unverified.
Native local app + prepared ticket + import wizard have been tested; the newly
rewired AutoCAD command and installer passed a live installed control acceptance.

**Native launch and local handoff verified on a control drawing:** the build registers
`GAOPEN` and an owned Green Atlas menu item. It asks to prepare a separate copy
(including unsaved changes), serializes the current database to a private DXF,
reads it into a separate AutoCAD database and binds available XREFs
in the copy, exports DXF/native evidence, shows partial-data notices, creates a
hashed ticket. The source is not saved. Historical tests below used the old
companion; they prove native preparation, not the new local-app launch.
The current Mac installation was updated through the installer UI, with the prior
package retained. AutoCAD autoload, restored menu after document opening, menu
invocation/cancel and GAOPEN → native copy → companion were verified on a control
DXF. Its source hash and DBMOD remained unchanged; two REGIONs, nested transforms
and a hole matched the native source exactly. A second control created with
AutoCAD's own XREF Attach command now verifies bind: both REGIONs, all loop
coordinates, areas and perimeters match the pre-bind native source exactly.
The historical generated XREF control has a different dependent-block flag;
AutoCAD omits its definition during serialization. A separate recovery module
now inventories live INSERT handles, detects the loss, and asks whether to
restore available references in the copy, skip them, or cancel. Recovery never
writes source objects or replaces an already valid reference. The restore path
was verified on that previously failing control: transformed loop coordinates,
areas and perimeters match fresh native source evidence exactly. Skip/cancel
coverage and more complex reference graphs still need acceptance testing.
The WBLOCK path was removed after a live test caught it changing source DBMOD.
The installed 0.1.21 control also launched the bundled local app, created the
project, completed native intake and full-source preparation without changing
the source or child XREF. Version 0.1.22 fixes stale WKWebView operation status
and explicit retry after an interrupted published handoff. This does not prove
arbitrary XREF/dataset coverage. Signing/notarization
and Windows delivery remain unverified.

A native Mac installer is built on a qualified development machine with:

```sh
bash tools/autocad-bridge/build-desktop-runtime.sh
# Use the exact runtime directory printed by the preceding build:
bash tools/autocad-bridge/build-desktop-macos.sh /absolute/built/runtime/directory
# Use the exact app path printed by that build (no GADevelopmentProfile):
GREEN_ATLAS_DESKTOP_APP='/absolute/Green Atlas.app' bash tools/autocad-bridge/build-macos.sh
# Use the exact native package directory printed by that build:
bash tools/autocad-bridge/package-macos.sh /absolute/GreenAtlasBridge.package.bundle
```

Each build uses an isolated directory; no previous build is deleted. Packaging
produces a relocatable `Green Atlas Installer.app` and ZIP in an isolated
`.runtime/autocad-bridge/distribution/build-*` folder. The installer needs only
macOS system frameworks, not Python, Xcode, the SDK or this checkout. It installs
for the current user into Autodesk's `ApplicationAddins` directory, preserves
the previous version on update, refuses unrelated/symlink destinations and
offers recoverable removal through Trash. Local projects live separately under
`~/Library/Application Support/Green Atlas/Workspace`, outside the installed
package. It asks the user to close AutoCAD and Green Atlas
instead of terminating it.

Each package build runs the installer implementation's `--self-test` in a
disposable directory: first install, identity, update/backup, source preservation,
unrelated-target refusal, symlink refusal, damaged-code/runtime rejection,
missing-app rejection and development-profile rejection. This does
not replace the user's installed plugin. The native installer window has also
been opened and visually inspected on the current Mac.

Default builds are **ad-hoc signed local tests, not public releases**.
`GREEN_ATLAS_SIGNING_IDENTITY` can select the publisher's signing identity on
the build machine. Public distribution still requires valid Developer ID
signing and notarization. No Gatekeeper/AutoCAD security setting is disabled.
Windows packaging is not implemented yet. The new Mac command still requires
diverse real-DWG, calculation/export and clean-profile acceptance. Use
`docs/implementation/2026-09-20-desktop-local-runtime.md` for exact tested slices.

Read-only ObjectARX 2027 bridge for making AutoCAD the authoritative geometry
provider for Green Atlas imports. The web service consumes a verified native
snapshot; it must not reinterpret calculation geometry with a second DXF
parser.

## Historical 0.1.28 native bridge state

The macOS arm64 bundle is implemented and exercised in AutoCAD 2027.0.1. The
admitted path is:

1. `GAEXPORTSNAPSHOTFILE` opens the exact saved DXF in an isolated side database.
2. The native traversal records every model-space entity instance, including
   nested `INSERT` descendants and attached `ATTRIB`, with a handle and complete
   instance chain.
3. AcBr emits ordered REGION loops in local definition coordinates. The bridge
   applies the complete nested `INSERT` affine transform to each curve before
   sampling, so rotated and non-uniformly scaled instances are represented in
   WCS without asking ASM to scale the `AcDbRegion` itself. Area is transformed
   by the region-plane Jacobian and perimeter is measured on transformed AcGe
   curves.
4. Independent verifiers compare coverage and topology with separately
   generated inventories.
5. `compile_autocad_region_probe.py` admits the raw probe into the hashed
   `green-atlas.autocad-snapshot/1` contract.
6. Green Atlas admits only geometry carried by the native snapshot. Version
   0.1.20 emits REGION/HATCH topology, finite `AcDbCurve` paths, native
   `AcDbMline` axis paths and `AcDbPoint`
   coordinates. Remaining calculation-relevant types must be added here,
   rather than delegated to a second importer.
   HATCH boundary APIs are quarantined from the in-process traversal. Real
   municipal drawings have demonstrated fatal AutoCAD signals in both
   `getRegionArea()` and `getLoopAt()` for malformed elliptical loops; those
   failures cannot be caught by C++. HATCH instances therefore remain explicit
   unresolved coverage while safe REGION and curve geometry continues through
   the partial-data workflow. This favours opening every drawing over risking
   the whole editor for decorative fill boundaries.
   Lightweight polylines are sampled from AutoCAD's authored line/arc
   segments, `AcDbLine` uses its exact transformed endpoints and `AcDbMline`
   uses its exact axis vertices as the setback centreline. A line with
   no length in the admitted WCS XY projection is explicit presentation
   context, not a failed or invented obstacle.
7. The local `green-atlas-autocad` MCP server submits preparation requests to
   the ObjectARX plugin running in full AutoCAD and then invokes the admission
   compiler. MCP does not parse or approximate CAD geometry.

Unsupported geometry is not treated as empty space. Unreadable block
records/entities, malformed MINSERT data and unresolved/unloaded XREFs are
explicit traversal blockers. The
compiler rejects such a probe instead of publishing partial native evidence.

The compiler admits historical probes from safe bridge versions 0.1.4 through
0.1.20. It requires every known traversal diagnostic and independently
reconstructs area and perimeter from the emitted loops before producing a
snapshot. Versions 0.1.6 through 0.1.20 can admit an XREF traversal: each contributing drawing is
recorded by package-relative path, byte size and SHA-256, referenced from the
coverage ledger and re-hashed by the service before publication.

## Build

The official ObjectARX 2027 SDK is expected at `.local/objectarx-2027` and is
not committed. AutoCAD provides the runtime frameworks.

```sh
tools/autocad-bridge/build-macos.sh
```

Output:

```text
.runtime/autocad-bridge/macos/GreenAtlasBridge.bundle
.runtime/autocad-bridge/macos/GreenAtlasBridge.package.bundle
```

The build is universal arm64/x86_64, C++17 and ad-hoc signed.
`PackageContents.xml` and the bundle metadata are pinned to AutoCAD R26.0 /
plugin 0.1.20. The package owns the local MCP request queue. AutoCAD for Mac's
Autoloader loads `green_atlas_loader.lsp` for each document and the idempotent
bootstrap resolves and loads the adjacent ObjectARX bundle once per process.
It does not read or transform CAD geometry.

## Local MCP installation

Install the generated package for the current user:

```sh
ditto .runtime/autocad-bridge/macos/GreenAtlasBridge.package.bundle \
  "$HOME/Library/Application Support/Autodesk/ApplicationAddins/GreenAtlasBridge.bundle"
```

`ApplicationAddins` is the AutoCAD for Mac autoloader directory. The similarly
named `ApplicationPlugins` user directory is the Windows deployment convention
and is not scanned by AutoCAD for Mac.

Register the stdio server in Codex:

```sh
codex mcp add green-atlas-autocad -- \
  "$PWD/apps/api/.venv/bin/python" \
  "$PWD/tools/autocad-bridge/mcp/green_atlas_autocad_mcp.py"
```

Restart AutoCAD once so the plugin is loaded, then start a new Codex task so
its MCP tool inventory includes `autocad_bridge_status` and
`autocad_prepare_dxf`. The official Autodesk AutoCAD MCP Tech Preview is
Windows-only and currently targets Autodesk Assistant; the community servers
audited for this task also require Windows COM/.NET. This local adapter exists
to give Codex the same structured tool boundary on AutoCAD for Mac without
introducing another geometry reader.

## Extract and compile a snapshot

Load the bundle in AutoCAD, open the saved DXF and run:

```text
GAEXPORTSNAPSHOTFILE
```

The command does not traverse the potentially modified live document. It
imports the DXF into a separate `AcDbDatabase`, requires known drawing units,
hashes the source bytes and writes atomically:

```text
<source>.dxf.green-atlas.geometry.json
```

Compile and revalidate the probe:

```sh
PYTHONPATH=apps/api apps/api/.venv/bin/python \
  scripts/cad-lab/compile_autocad_region_probe.py \
  '<source>.dxf.green-atlas.geometry.json' \
  --autocad-version 2027.0.1 \
  --target macos-arm64 \
  --package-root '<directory containing the full CAD package>'
```

With no output argument, the compiler writes the deterministic adjacent file:

```text
<source>.dxf.green-atlas.snapshot.json
```

The full prepared-import flow discovers that exact name automatically. It
binds the sidecar by path, file SHA-256, embedded source SHA-256 and payload /
per-geometry hashes, then rechecks the sidecar immediately before atomic
publication. A missing or invalid native snapshot blocks import; it does not
fall back to another parser. The browser upload requires one adjacent snapshot for
every DXF.

## Sequential real-file matrix

`scripts/cad-lab/audit_autocad_native_matrix.py` runs the same MCP preparation
implementation over a manifest, strictly one DWG or DXF at a time. It never reads CAD
geometry itself and has no parser fallback. After every source it atomically
writes source/snapshot hashes, native admission counts, elapsed time and the
exact rejection reason, so a later failure does not erase earlier evidence.

```json
{
  "schema": "green-atlas.autocad-native-matrix-input/1",
  "drawings": [
    {
      "id": "street",
      "source_path": "/absolute/path/street.dxf",
      "package_root": "/absolute/path/full-package",
      "timeout_seconds": 900
    }
  ]
}
```

For an exhaustive diagnostic inventory with interruption-safe partial evidence, point the same manifest
at a dataset root. Files are traversed in stable relative-path order and each
result is published before the next drawing starts:

```json
{
  "schema": "green-atlas.autocad-native-matrix-input/1",
  "roots": [
    {
      "id_prefix": "lct",
      "path": "/absolute/path/to/dataset",
      "extensions": [".dwg", ".dxf"],
      "timeout_seconds": 900
    }
  ]
}
```

```sh
apps/api/.venv/bin/python scripts/cad-lab/audit_autocad_native_matrix.py \
  manifest.json report.json
```

For a long dataset run, `--resume` reuses only admitted rows whose source and
snapshot hashes still match. Rejected, missing or changed rows are attempted
again; a different manifest or installed bridge version refuses the old report.

The runner refuses a stale loaded plugin version before submitting any source.

## Commands

- `GAEXPORTPROBE`: early root inventory/measurement diagnostic.
- `GAEXPORTREGIONPROBE`: topology from the current live database. A non-zero
  `DBMOD` makes it diagnostic only; the compiler will not admit it.
- `GAEXPORTSNAPSHOTFILE`: product preparation path using an isolated DXF side
  database. This is the only currently admissible native capture mode.

The raw REGION file intentionally has `complete: false`: it is evidence to be
verified, not the final snapshot contract.

## Verified Kustanay reference

For source SHA-256
`39afb6bb4dfb93b0b37e943c60a0047116ac1d39d3156459403cb19723dbd587`:

- independent all-entity coverage: 121,369 expected and 121,369 actual;
- REGION instances: 2,384 expected, resolved and independently compared;
- loops / sampled points: 2,384 / 18,293;
- maximum WCS coordinate error: `3.637978807091713e-12` source units;
- traversal blockers: 0;
- service features: 90,561 before and 92,945 after native merge;
- all six REGION-bearing layers become geometry-complete;
- provider time: 5.69 s in the 0.1.6 regression run.

Receipts are under
`.runtime/kustanayskaya-mac-ready-20260917/` and are intentionally local.
These numbers document the superseded 0.1.6 migration experiment: its 104,835
`unresolved` records were temporarily supplied by the portable reader. They do
not describe the current product path and must not be used to justify a fallback.
The current bridge must emit every calculation-relevant object itself or reject
the drawing with explicit coverage evidence.

The figures above were first established with 0.1.3 and reproduced with 0.1.4
and 0.1.6 on the same source SHA. The 0.1.6 run again yields 92,945 features
and six complete REGION layers. Its snapshot payload SHA-256 is
`ebc34eaa573abd8fed201e48ee4bd4e04f3bef58ad47d3104d6352630bdfabd4`.

## Native control corpus

Verify native output, compiler admission and provider merge:

```sh
PYTHONPATH=apps/api apps/api/.venv/bin/python \
  scripts/cad-lab/verify_objectarx_native_controls.py \
  .runtime/objectarx-native-controls-20260917/native-region-positive-autocad.dxf \
  .runtime/objectarx-native-controls-20260917/negative/negative-controls.json \
  --output .runtime/objectarx-native-controls-20260917/verification-v1.json

PYTHONPATH=apps/api apps/api/.venv/bin/python \
  scripts/cad-lab/verify_objectarx_minsert_control.py \
  .runtime/objectarx-native-controls-20260917/native-region-positive-autocad.dxf.green-atlas.regions.json \
  /private/tmp/green-atlas-minsert-positive.dxf \
  .runtime/objectarx-native-controls-20260917/native-region-minsert-positive.manifest.json \
  --output .runtime/objectarx-native-controls-20260917/minsert-verification-v015.json

PYTHONPATH=apps/api apps/api/.venv/bin/python \
  scripts/cad-lab/verify_objectarx_xref_control.py \
  .runtime/objectarx-native-controls-20260917/resolved-xref-dwg-positive/resolved-xref-positive.manifest.json \
  .runtime/objectarx-native-controls-20260917/native-region-positive-autocad.dxf.green-atlas.regions.json \
  .runtime/objectarx-native-controls-20260917/resolved-xref-dwg-positive/resolved-xref-positive.dxf.green-atlas.regions.json \
  .runtime/objectarx-native-controls-20260917/resolved-xref-dwg-positive/verification-v016.json
```

Historical native controls verified through 0.1.6:

- root REGION and a nested REGION with a hole are both admitted;
- the nested instance combines parent/child rotation and non-uniform scale;
- expected WCS area/perimeter are 900/210 and independent topology verification
  differs in area by less than `4e-12`;
- provider merge adds both REGION geometries and leaves no incomplete layer;
- MINSERT 2×2 expands four cell-specific provenance chains; its rotated grid
  offsets remain unscaled by block scale and matched the independent migration
  oracle within `5.69e-14` source units;
- an unresolved XREF fails closed even when `isUnloaded()` is false;
- a loaded DWG XREF contributes two REGION instances with exact dependency
  SHA/size/path provenance; independent affine verification has maximum
  coordinate error `8.04e-14` and zero area error.

## Honest boundary

- Native geometry output currently covers REGION, HATCH areas that AutoCAD can
  convert to a native region, finite AcDbCurve instances and AcDbPoint. Other
  non-context calculation entities remain explicit incomplete coverage.
- Valid MINSERT grids are expanded per cell; malformed row/column/spacing data
  fails admission closed.
- Unloaded XREF contents cannot be recovered without the referenced files.
- AutoCAD 2027 loaded the DWG-child positive control, but rejected the same
  dependency supplied as DXF. Autodesk documents drawing XREFs specifically as
  DWG files. The product boundary remains DXF-only, so the DWG control proves
  the bridge capability but is not an accepted service package. Multiple input
  DXFs must be modelled as independently hashed drawings on one map unless an
  upstream process supplies a bound root DXF; they must not be relabelled as
  native AutoCAD XREFs. Evidence and official links are recorded in
  [the XREF format boundary note](../../docs/research/2026-09-17-autocad-bridge/xref-format-boundary.md).
- Semantic layer classification is outside the bridge; a valid contour is not
  automatically a building, road, network or project boundary.
- Kustanay is one real reference, not proof for every DXF. Rotated/scaled nested
  REGION, holes, MINSERT and a DWG XREF now have native positive controls. A
  product-valid multi-DXF composite control and other real streets remain
  required before the overall LCT scenario can be declared complete.
