# Green Atlas AutoCAD bridge

Read-only ObjectARX 2027 bridge for extracting geometry that the portable DXF
reader cannot project reliably. AutoCAD is an optional offline preparation
tool and an independent verifier; it is not a Linux service dependency.

## Current state: 0.1.6

The macOS arm64 bundle is implemented and exercised in AutoCAD 2027.0.1. The
admitted path is:

1. `GAEXPORTREGIONFILE` opens the exact saved DXF in an isolated side database.
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
6. Green Atlas merges only the admitted native geometry into the ordinary DXF
   result. All other objects keep using the portable reader.

Unsupported geometry is not treated as empty space. Unreadable block
records/entities, malformed MINSERT data and unresolved/unloaded XREFs are
explicit traversal blockers. The
compiler rejects such a probe instead of publishing partial native evidence.

The compiler admits raw probes from safe bridge versions 0.1.4, 0.1.5 and
0.1.6. It requires every known traversal diagnostic and independently
reconstructs area and perimeter from the emitted loops before producing a
snapshot. Only 0.1.6 can admit an XREF traversal: each contributing drawing is
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
```

The build is arm64, C++17 and ad-hoc signed. `PackageContents.xml` and the
bundle metadata are pinned to AutoCAD R26.0 / plugin 0.1.6.

## Extract and compile a snapshot

Load the bundle in AutoCAD, open the saved DXF and run:

```text
GAEXPORTREGIONFILE
```

The command does not traverse the potentially modified live document. It
imports the DXF into a separate `AcDbDatabase`, requires known drawing units,
hashes the source bytes and writes atomically:

```text
<source>.dxf.green-atlas.regions.json
```

Compile and revalidate the probe:

```sh
PYTHONPATH=apps/api apps/api/.venv/bin/python \
  scripts/cad-lab/compile_autocad_region_probe.py \
  '<source>.dxf.green-atlas.regions.json' \
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
publication. A missing sidecar leaves the portable Linux/Docker path unchanged.
The ordinary upload endpoint also accepts the same snapshot as the optional
`cad_snapshot` multipart part.

## Commands

- `GAEXPORTPROBE`: early root inventory/measurement diagnostic.
- `GAEXPORTREGIONPROBE`: topology from the current live database. A non-zero
  `DBMOD` makes it diagnostic only; the compiler will not admit it.
- `GAEXPORTREGIONFILE`: product preparation path using an isolated DXF side
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
The 104,835 `unresolved` native coverage records are not 104,835 lost objects:
version 0.1.6 emits native geometry only for REGION, while the ordinary reader
continues to handle standard LINE, ARC, HATCH, text and other supported types.

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

Verified through 0.1.6:

- root REGION and a nested REGION with a hole are both admitted;
- the nested instance combines parent/child rotation and non-uniform scale;
- expected WCS area/perimeter are 900/210 and independent topology verification
  differs in area by less than `4e-12`;
- provider merge adds both REGION geometries and leaves no incomplete layer;
- MINSERT 2×2 expands four cell-specific provenance chains; its rotated grid
  offsets remain unscaled by block scale and match an independent ezdxf model
  within `5.69e-14` source units;
- an unresolved XREF fails closed even when `isUnloaded()` is false;
- a loaded DWG XREF contributes two REGION instances with exact dependency
  SHA/size/path provenance; independent affine verification has maximum
  coordinate error `8.04e-14` and zero area error.

## Honest boundary

- Native geometry output currently covers REGION only.
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
