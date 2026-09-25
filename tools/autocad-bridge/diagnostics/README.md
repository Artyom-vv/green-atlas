# Native AutoCAD diagnostics (not an alternative product importer)

Current priority: **direct native queries**, not capture-converter development.
Newest: [whole-window native predicate](../../../docs/implementation/2026-09-23-native-window-query-experiment.md).
`run_xref_native.py --window-case <manifest>` inventories every available leaf
in the loaded host/XREF tree using native AABB broad phase. An explicit optional
calculation fixture applies the shared `AffineAreaQuery` and native curve
queries. Unknown roles/open areas remain explicit; all nonblocked points are
DRAFT. The Kustanayskaya fixture is research policy, not a new classifier.
`summarize_window_queries.py` compares the same grid with the old three-object
experiment. Evidence-stage12 covers DWG/DXF; stage13 reruns Kamchatskaya's
41-case baseline after sharing the query interface. No installed-plugin changes.

See [native-affine measurements](../../../docs/implementation/2026-09-23-native-affine-query-experiment.md)
and the executed native-affine section below. The following capture series
is historical evidence, not an instruction to resume polygon conversion.

## Earlier reproducible DWG/DXF capture checks — 23 September

See [the measured report](../../../docs/implementation/2026-09-23-native-dxf-roundtrip.md)
before reusing the older experiments below. Plugin0.1.38 is installed;
that does not mean every referenced street passes the product handoff.
The full available Kamchatskaya DXF capture is1.08GB and the production
ticket writer rejects it at768MiB. The initial comparison omitted available
XREF files; neither its25 unresolved count nor its successful partial
calculation certifies the referenced street.

- `run_dxf_roundtrip.py --source DWG --bundle BUNDLE --output NEW_DIR`:
  copies the input, native DXFOUT, fresh Core Console DXF reopen/capture,
  per-object status/REGION comparison. Repeat `--xref KNOWN_DWG` for actual
  dependencies; no fuzzy file matching or CAD parsing outside AutoCAD.
- `capture_native_file.py`: new native replay on a fixed DWG/DXF and
  explicit companions. All source/reference hashes must remain unchanged.
- `roundtrip_comparison.py BEFORE AFTER --output NEW_REPORT`: streaming
  evidence reads (including QA gzip); region measurement comparison does
  not retain whole coordinate graphs. Structure equality is not safety.
- `profile_native_capture.py CAPTURE --output NEW_REPORT`: writer-layout
  byte/coordinate accounting with summary checks; optional `--ticket-writer`
  exercises production admission on the uncompressed actual capture.
- `run_native_cases.py`: bounded native per-handle/pair diagnostics, including
  native DXF input. No source save, endpoint snapping or alternate parser.

DXF conversion here tests format compatibility; GAOPEN still transfers the
live AutoCAD database directly. QA gzip is not a new product payload format.
Run scripts with `apps/api/.venv/bin/python`, one street at a time, after
checking disk space. Preserve receipts even for failures/abnormal Core exit.

## Earlier native containment experiment

This developer module checks explicit objects from the real LCT dataset using
AutoCAD ObjectARX. It does not change GAOPEN, classification, placement, the
snapshot compiler, or the installed product bundle. No ezdxf/alternate CAD reader
is involved. Existing snapshot coordinates only help choose locations; they are
not an independent truth oracle.

## Build and run

1. `bash tools/autocad-bridge/diagnostics/build-macos.sh` creates a unique, signed,
   arm64-only diagnostic bundle under `.runtime/autocad-bridge/diagnostics/`.
2. Run `apps/api/.venv/bin/python tools/autocad-bridge/diagnostics/prepare_containment_cases.py
   --output artifacts/native-containment-20260922/<new-pass>`.
3. Load that exact bundle in AutoCAD with `arxload`. Do not replace/unload the
   production bridge or weaken trusted-path settings. The Load warning requires
   operator approval when using Computer Use.
4. Run `GACONTAINFILE`, then enter the absolute `request.txt` path.
5. Read `native-report.json`; independently inspect the same native objects in
   AutoCAD. Do not equate a successful build with native execution/acceptance.

## Request format

UTF-8 lines: absolute DWG path; NEW output `.json` path; case count. Each case:
name; handle count; one hexadecimal handle per line; point count; one XYZ point
per line. Points are in the source database's WCS/drawing units. Supported
temporary-region inputs match the SDK's documented `createFromCurves` whitelist.
An existing REGION is queried directly; HATCH uses `getRegionArea`. No endpoint
snapping, forced closure, projection, native entity explosion or repair is done.

The probe keeps the source entities open **for read** while invoking the native
API. It records conversion and individual query statuses, including partially
created regions on a non-success conversion status. Non-success query results
are null, never falsely interpreted as outside/free space. Ownership of native
temporaries is explicit; working database is restored before source destruction.
Source SHA is checked before/after. Existing report files are not overwritten.

`GREEN_ATLAS_DIAGNOSTIC_ARCH=x86_64 bash tools/autocad-bridge/diagnostics/build-macos.sh closure`
builds the isolated `GACLOSEFILE` experiment. It uses the same request format,
but clones each single `AcDbPolyline` in an in-memory side database, tries
`makeClosedIfStartAndEndVertexCoincide`, then explicitly calls `setClosed(true)`
on the clone if still open. It reopens the temporary entity **for read** before
`createFromCurves`: Autodesk documents a crash risk for write-open inputs.
This is a deliberate hypothetical chord, **not** a production repair. The
source file and original entity are never saved or mutated; compare
`source_unchanged`, actual `clone_closed_after_explicit_request`, native
`conversion_status` and the returned BRep. `eOk` from the conditional close
alone does not mean that the clone is closed (the SDK says it can no-op).
See `docs/implementation/2026-09-23-native-hatch-boundary-calculation.md` for
the real Natashinsky/Berzarina negative results and positive same-file control.
The same isolated closure variant can test a narrowly identified initial spur
on an in-memory clone; it records exact native segment intersections, full
BRep traversal, and the production `extractRegionTopology` result. This is a
review candidate, not a source modification or an approved project boundary.
`validate_closure_candidate.py <report>` checks the result against the current
product planar-area admission. `validate_native_project_flow.py --live-capture
--calculate --native-site-candidate-report <report>` tests only a **simulated**
operator approval and partial-source acceptance in an unsaved project. It
requires the report SHA to match the original DWG and retains the source path
as visible reference; it does not install a plugin, publish a project, or
claim complete planting safety.
The same validator accepts `--case open_building_78B3` for an explicitly
closed clone. `validate_native_project_flow.py --live-capture --calculate
--site-layer 'Граница заказа' --native-building-candidate-report <report>
--native-building-candidate-case <case>` compares a shrub point before and
after an **unsaved** proposed building area. The diagnostic layer is marked
synthetic, and the original open path stays visible and incomplete. The
Kustanayskaya cases `78B3`, `ED22`, `11769` and their mixed placement effect
are documented in `docs/implementation/2026-09-23-open-building-coverage.md`.

`bash tools/autocad-bridge/diagnostics/build-macos.sh hatch` creates a separate
`GreenAtlasHatch.bundle`, command `GAHATCHFILE`. It reports hatchStyle, typed
loopType and typed polyline vertex/bulge metadata for explicit handles; it does
not call the unsafe untyped edge-pointer overload. The prepared Natashinskiy
multi-outer request is in
`artifacts/native-containment-20260922/pass-16-hatch-multiouter-natashinskiy/`.
This candidate bundle is compiled/signed but **not loaded/executed**; see
`docs/implementation/2026-09-23-disjoint-hatch-coverage.md`.

The current diagnostic also records `curve_area_status` and
`curve_area_with_implicit_chord_units2` for each source `AcDbCurve`. Autodesk's
`getArea()` measures an **open** planar curve as though a straight segment
joined its endpoints. This is a measurement of a hypothetical closure, not a
native closed REGION or an approved building footprint; it must never silently
enter planting constraints. This field was exercised on Natashinsky and
Berzarina in AutoCAD Core Console on 23 September; see the same report above.

## Pass 01 scope and limitations

Dataset: Kustanayskaya `00.1_10004141_Топография.dwg`, source SHA
`56847d3f9f72f0b1af17cebe2d5c88c7536b171126df3a3097c0e1ed25e85590`.
Handles: `7BF` (coincident-endpoint control; native isClosed=false), `78B3` (open, approximately 14.5 mm endpoint
gap in preceding evidence), `6375` (open portion of a building outline).

Executed pass 01: 7BF produced a native REGION and five successful containment
queries; 78B3 and 6375 returned eInvalidInput with no regions. Their ten planned
point queries therefore did NOT run. For this planar REGION, an interior point
returns `on_boundary` with `AcBrFace`, whereas the edge point returns `AcBrEdge`.
Never interpret the enum alone as polygon-edge classification. The snapshot's
derived closed flag is not necessarily the authored AutoCAD flag. Full evidence
and visual checks: `docs/implementation/2026-09-22-native-containment-experiment.md`.

The experiment does not yet qualify the host XREF transform, constituent curve
grouping, broken-outline recovery, or a service placement. The exact historical
screenshot-to-handle correspondence is not established. The old user project
`5a2b4a57-d181-4156-aa65-d2773d30258a` still contains a 0.1.30 live snapshot;
it must not be mistaken for the 0.1.33 saved-DWG probe.

AutoCAD is an in-process native host: C++ exception handling does not isolate a
kernel crash. No untyped hatch edge-pointer APIs or internal ASM pointers are
called. Preserve all evidence if a native operation fails; isolate that case
before widening the experiment.

## Pass 02: surface availability on three other streets

`prepare_surface_cases.py --output <new-directory> [--streets 4. 6. 16.]`
prepares exact definitions from the recorded 0.1.33 dataset audit. It checks DWG
SHA and retains prior status/reason/instance identity. Requests still use the same
loaded GACONTAINFILE; no document opening, zoom, selection or source saving needed.

`summarize_surface_cases.py <case-directories...> --output <new-summary.json>`
compares actual native responses, source hashes and known-hole control points.
Rejected objects' origin probes are explicitly unqualified: a successful query
at zero is NOT spatial acceptance of their islands or holes.

Measured: 12/14 rejected definitions yielded positive-area native regions;
three accepted hole controls matched 9/9 points. The extra14F30F follow-up was
previously accepted but its sampled polygon self-intersects and getRegionArea
returns null. See `docs/implementation/2026-09-22-native-surface-experiment.md`.
This is not a production replacement or full-dataset acceptance.

Protocol caveat: for HATCH, conversion_status3 on a null pointer is the probe's
default value, NOT an ErrorStatus returned by getRegionArea. Summaries label it
`getRegionArea_returned_null`. For createFromCurves the status is directly returned.

## Pass 03: topology diagnostic (executed)

`bash tools/autocad-bridge/diagnostics/build-macos.sh topology` builds a separate
`GreenAtlasTopology.bundle`, command `GATOPOFILE`. It uses the same explicit input
protocol and read-only side database; no production module replacement. The
already loaded containment diagnostic remains available for comparison.

Each successful BRep receives `topology_probe`: native face/bound/loop traversal
statuses, loop roles and edge-curve samples. No single-face restriction is used.
The per-region traversal budget is 4096 faces/loops/edges combined; exhausted
budgets and traversal failures are visible, not silently accepted as complete.
Sixteen parameter segments per edge are **fixture hints only**, with no claimed
maximum geometric error. They must not enter the production geometry pipeline
or be treated as an independent correctness oracle.

Prepared inputs: `artifacts/native-containment-20260922/pass-03-topology/{4,6,16}`.
After explicit approval, the new binary was loaded and all three requests ran.
All 12 recovered definitions have traversable native faces/loops. The diagnostic
point preparation script `prepare_topology_points.py` found three unusable sampled
fixtures (14F2D2,138AA6,143E46), which remain unqualified, not repaired.
Point run `pass-03-points-c` matched 41/41 expected responses on 9 recovered
definitions + 3 controls. This is sample-fixture consistency, not independent
geometric acceptance. Details: docs/implementation/2026-09-22-native-topology-experiment.md.
Host transforms and product placement remain outside this experiment.

## Pass 04: native seam evidence (built, awaiting load approval)

Update: pass04 executed after approval; see native-seams-experiment.md.
Pass05 grid also executed; see native-grid-experiment.md.

Build with `build-macos.sh seams`: separate GreenAtlasSeams / GASEAMFILE,
not a replacement of the loaded topology or production modules.
`edge_probe.cpp` adds native vertex coordinates and loop-local identities
(AcBrEntity.isEqualTo), edge-to-curve and edge-to-loop orientation statuses,
oriented curve interval and its endpoint/evaluation coordinates.
No vertex snapping, curve reversal, geometry repair or changed production route.
Loop-local vertex IDs are diagnostic identities, not persisted database handles.

Inputs: `artifacts/native-containment-20260922/pass-04-seams/{6,16}/request.txt`.
Built binary SHA: `bbe4e4876a57112450ce998199dfe9ade5b0388cb9420c8599b3d33e9213b780`.
Not yet loaded/executed. Compare matching native vertices vs oriented curve ends;
check all returned statuses before concluding that a seam is open or closed.

## Pass 06: distance (prepared, not executed)

Build variant `distance`, command GADISTFILE; distance_probe.cpp traverses native
unique BRep edges and queries bounded curves, with no polygon assembly.
`prepare_native_distance.py <pass04> <new-output>` prepares 37 queries on5objects.
See docs/implementation/2026-09-22-native-distance-experiment.md for acceptance
limits, binary SHA and current load-approval state. A positive edge distance does
not mean the point is outside the occupied region or placement is permitted.
## Current native-affine series (23 September, executed)

`build-macos.sh xref` includes `xref_affine_controls.cpp` in the isolated
diagnostic module; it does **not** change the installed plugin. Use
`run_xref_native.py --areas --area-cases <manifest> --clone-copies` with the
complete package and explicit XREF map, then repeat with `--dxf` in a new
output directory. No source SAVE, GUI interaction, sampled polygon export,
or alternate CAD parser. Run heavy CAD processes sequentially.

Read `docs/implementation/2026-09-23-native-affine-query-experiment.md` for
the exact tested binary, manifests, evidence and limitations. On three
streets: Kamchatskaya41/41, Natashinskiy32/34 (B86/B8D native null retained),
Kustanayskaya26/26, identical recorded DWG/DXF answers. These are selected
cases, not whole-street or product acceptance. Some workers still exit254
after completed commands; receipts distinguish this from geometry results.

`native_area_controls[].passed` retains the original world-REGION baseline;
`affine_probe.passed` is the separate native-local-membership/world-NURB
experiment. Never conflate them. `prepareLocalArea` shares native acquisition
and correct database-active BRep initialization. The new probe tests every
selected case, not just baseline failures. Planar-only; native fit tolerance,
edge witnesses, straight-edge analytic distance oracle and repeated answers
are all recorded. It does not repair an open curve or suppress an API refusal.

`archive_xref_evidence.py` preserves reports/logs/hashes and compares selected
observations with absolute tolerance1e-8, ignoring only timing. It is a
**reporter**, not a test runner: exit0 alone does not mean all cases passed.
Inspect `affine_probes.failures` and `comparisons.*.fields.*.difference_count`.
Redirect its verbose stdout to a log. Preserve earlier failed runs.
