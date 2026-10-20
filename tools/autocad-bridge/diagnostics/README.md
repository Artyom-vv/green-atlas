# Native AutoCAD diagnostics (not an alternative product importer)

## Live capture invalidation qualification, 2 October

`live_capture_probe.cpp` registers the explicit GUI command
`GALIVECAPTUREPROBE`. Build it with `capture_session_run.build(...,
architecture="arm64")` for a native ARM64 editor. It calls the selected
uninstalled production library's `openSession`/`closeSession`, without that
library's entry point, menus, archive/save or application launch. A request
contains four lines: built library path, private output directory, copied
source file, and a new 32-character hexadecimal token. An optional fifth line
selects an explicit mode; omitted means `capture` and preserves the ordinary
read-only qualification request.

The library must be under this checkout's `.runtime/autocad-bridge/macos`;
the output must be an owned 0700 directory under `output`; the active source
must match a file inside that output directory's `input`. Copy/hash the whole
dependency package before opening it. Never invoke this on a user's original
document or run it concurrently with another CAD qualification.

Live capture publishes a bounded `<snapshot>.session-diagnostics.json` with
the capture phase, document/working database predicates, read-only DBMOD,
reactor event counts and the first 32 native object identities. The probe
temporarily enables `GA_LIVE_CAPTURE_DIAGNOSTICS=1` for one first-event stack
(24 frames), then restores its prior value. The entire diagnostic is capped
at 64 KiB. Diagnostics never clear invalidation or make a failed capture
acceptable; the existing live wire error remains unchanged during this
causal qualification. Inspect the sidecar before inferring a cause from
`live_source_changed_or_inactive`.

Later session validation also republishes a failure in its actual context.
Archive phases distinguish admission, document locking, admission while
holding the lock, and package cloning. A failed check is written before
unlocking the document so working database changes cannot be hidden by
cleanup. Logging changes neither the validation predicates nor their errors.

For genuine edit controls, use separate new owned directories. The explicit
`seed-control` mode creates only `root/input/host-control.dwg` in an independent
native database: metres, one LINE from `(0,0,0)` to `(10,0,0)`, no XREF. It saves
this newly generated fixture, never the active document, and writes a marker
with its SHA. Existing source files/markers are refused. Open that fixture,
then invoke `append-point-control` or `move-line-control`: the former appends
a POINT at `(17,23,0)`; the latter moves the LINE endpoint to `(12,0,0)`.

Both edit modes require the native-seeded filename, marker, unchanged disk
SHA, exact in-memory fixture structure and matching active document. They
first require a successful baseline session inspection, verify the actual
physical edit and unchanged context, and require the production inspector
to reject it with `live_source_changed_or_inactive`. A missing/failed baseline,
wrong rejection or unexpected geometry is a failure. `control-receipt.json`
records verified entity counts/coordinates, DBMOD before/after, source hash
and rejection. The actual edit remains unsaved: close this owned fixture with
discard. Do not erase the test entity or reset DBMOD. This control is never
permitted on Olympic Village, Kustanayskaya or an arbitrary copied drawing.

The explicit `delivery-parity` mode runs the production
`native/delivery_command.cpp` rather than duplicating its sequence in a probe.
Its active document guards, working database scope, cancellation checks,
capture/copy/hash/archive checks and ticket validation remain in that source.
The selected uninstalled library implements confirmation, stage display,
live capture, archival, source issue review and the actual ticket writer.
Use a NEW owned output/input directory and matching active source copy.

Only three boundaries change: staging goes to `root/staging`, connector launch
is intercepted after the real `transfer.gatransfer` has been validated, and
the final error dialog becomes a diagnostic file after a failure. No desktop
or API is launched and no user project is published. `delivery-receipt.json`
records the selected binary hash, unchanged source hash, actual snapshot and
sidecar paths, UI stage/review counts and whether the native producer completed.
The production success message precedes an explicit
`PRIVATE_DELIVERY_COMPLETE` message indicating the intercepted launch.

This qualifies the native producer, including its real preparation modal and
stage windows. The stage implementation explicitly avoids pumping the event
loop; its display alone does not establish that it triggered database edits.
The selected library's entry point and idle callbacks are not installed.
Pending live/MCP request files or an active private MCP operation are refused
at entry, but already installed callbacks belong to their existing instance:
they are not qualified by the private preparation guard. Coordinate this GUI
run with all other CAD work and preserve diagnostics if a new request arrives.
A successful private producer does not explain a historical intermittent
failure or certify desktop handoff, calculations, release or the full product.

Archive diagnostics report the capture/current caller's `myLockMode()` and
whether archive ownership was borrowed or acquired. Registered modal commands
borrow their existing stable write lock; they neither reacquire nor unlock it.
Application-context archival still acquires its own lock and retains the
following validation. If acquisition changes a watched database, refusal is
required. Such a later archive cannot assume a lock change preserves a prior
capture; no long-lived lock or automatic recapture is added by this fix.

`test_document_lock_policy.cpp` is an executable check against SDK modes:
write, protected automatic write and exclusive write can be borrowed;
read, none, unprotected automatic write and unknown modes cannot. The explicit
`archive-write-control` GUI mode additionally requires the native-seeded tiny
fixture. It verifies actual modal ownership, runs the real capture/archive,
counts source document lock notifications and requires zero lock changes and
unchanged command ownership, geometry and disk hash. Its receipt also records
DBMOD without resetting it. Clone notifications remain valid invalidations;
this control does not demand a still-valid token after archival. Pair it with
the genuine point/line edit controls and actual street producer parity.

`live_topology_probe.cpp` runs SDK controls for
`ga::capture::validateLiveInstanceTopology` through a selected private library,
without its entry point or a product file importer. Build with
`capture_session_run.build(..., architecture="arm64")`, then reuse
`core_intake_run.run_stage(..., action="topology", drawing=None,
fixture="native-topology", database_mode="isolated", architecture="arm64")`.
The NEW owned 0700 root must be named `output/live-topology-controls-...`.
Coordinate all CAD jobs before invoking the Core harness.

The command seeds and reopens only its own synthetic files. Direct SDK reads
verify classes, handles, route lengths and line coordinates before checking
real preflight outcomes: ordinary INSERT passes; a 3 by 2 MINSERT refuses with
`array_instance`; a 64-element leaf route passes and a 65-element route refuses
with `instance_depth_limit`. For XREF, a loaded/resolved native database first
passes with its backing file present. Moving that owned file to an unsearched
name preserves loaded status and geometry but removes verified disk provenance;
preflight must refuse the actual reference with
`loaded_xref_disk_provenance_unavailable`. The file is restored and its hash
is checked. No alternate CAD reader or source save is used during validation.

`topology-controls.json` records the physical oracles and actual refusal
route/class/reason. Passing requires source hashes, native geometry and working
database/field evaluation restoration. Core must complete and exit zero:
artifacts from an abnormal exit do not qualify the controls. This proves these
specific producer guards, not complete fixture or street capture coverage.

## Core file intake experiment, 1 October

Authorized feasibility scenario: `core_intake_run.py --source FILE.dwg
--package-root DIRECTORY --production-library BUILT_NATIVE_BINARY
--output output/core-intake-.../NEW_RUN`.

`--architecture x86_64|arm64` selects both the diagnostic bundle build and
the Core process architecture on Mac. The default remains `x86_64` for
earlier experiments. Use an explicit matching architecture when comparing
against a running editor; process architecture alone does not explain a
different capture result. On 2 October the Kustanayskaya private archival
refusal 83 reproduced on ARM64 as well as x86_64. This option is diagnostic
only; it does not select or install a product import route.

The default `--database-mode isolated` reads a private saved drawing in an
explicitly owned database and restores working context and field evaluation
before confirming completion. `--database-mode active` retains the earlier
OPEN control for comparison; its abnormal shutdown is a failure, including
exit 254 after artifacts were written. No live GUI is contacted.
`--split-jobs` is an explicit control that separates capture from archival;
its saved-file identity comparison must pass. It did not resolve the measured
Kustanayskaya archival refusal and therefore is not the default path.

The directory tree and duplicate basenames are preserved. Only listed CAD,
font and reference asset extensions are copied; executable add-ins/scripts
are excluded. Symlinks, oversize packages and loaded XREFs outside the private
package are refused. There is no guessed relinking, repair, alternate parser
or automatic loading of intentionally unloaded references. This is not a
complete sandbox or a guarantee of all DWG/dependency types.

Without `--source`, `--fixture complete|missing|unloaded` creates private
controls. Every accepted capture must exit zero, preserve source hashes and
reopen its native archive in another Core after staging sources are hidden.
The comparison checks all instance mappings/classes/layers/transforms and
line/polyline vertices; it does not compare every HATCH/custom-object payload.
An exit-zero runner can return `needs_source_review`; inspect `receipt.json`,
never treat process success as calculation completeness.

`core_intake_project_check.py` runs a verified complete synthetic capture
through the real handoff, preparation, local planting, release and cold reopen
in a NEW `output/core-intake-...` QA database. Run with `PYTHONPATH=apps/api`.
Its existing transfer/5 envelope with an unavailable PID is explicitly a QA
adapter. It does not define a product Core provenance contract or install an
import UI. No experimental captures enter the user's project library.

`test_core_intake.py` covers missing/unloaded/proxy/unit refusals, unreadable
object identities, nested duplicate filenames, symlinks and output placement.
Ruff/mypy are run explicitly on these research modules rather than expanding
the backend baseline or excluding failures.

Current priority: **direct native queries**, not capture-converter development.
Newest: whole-window native predicate (локальный отчёт).
`run_xref_native.py --window-case <manifest>` inventories every available leaf
in the loaded host/XREF tree using native AABB broad phase. An explicit optional
calculation fixture applies the shared `AffineAreaQuery` and native curve
queries. Unknown roles/open areas remain explicit; all nonblocked points are
DRAFT. The Kustanayskaya fixture is research policy, not a new classifier.
`summarize_window_queries.py` compares the same grid with the old three-object
experiment. Evidence-stage12 covers DWG/DXF; stage13 reruns Kamchatskaya's
41-case baseline after sharing the query interface. No installed-plugin changes.

See native-affine measurements (локальный отчёт)
and the executed native-affine section below. The following capture series
is historical evidence, not an instruction to resume polygon conversion.

## Earlier reproducible DWG/DXF capture checks — 23 September

See the measured report (локальный отчёт)
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
