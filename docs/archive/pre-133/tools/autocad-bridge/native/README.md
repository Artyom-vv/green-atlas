# Native bridge: ownership and verification

This is the AutoCAD/ObjectARX integration, not another DWG/DXF parser.
The 22 September structural refactor preserved the 0.1.33 extraction rules.
Version 0.1.34 added a narrowly proven native two-polyline region and
`source_handles` provenance. Candidate 0.1.35 admits multi-face native REGION
topology and disjoint typed HATCH outer loops, with AutoCAD hatch-style island
semantics. A corrected 0.1.35 native-only candidate was replayed through
isolated AutoCAD Core Console on full Kamchatskaya and Berzarina DWG copies;
this is not the previously packaged desktop installer or a release check.
Native-only 0.1.36 additionally emits separately hashed, source-anchored
`area_proposals` for near-closed open polylines after AutoCAD validates a
temporary clone as one REGION. They are review evidence, never active
calculation geometry or edits to the DWG. Three full-street replays and the
remaining product gap are documented in
`../../../docs/implementation/2026-09-23-native-area-proposals.md`.

## Module boundaries

| Responsibility | Files | Does not own |
|---|---|---|
| AutoCAD module lifecycle and command registration | `green_atlas_bridge.cpp` | Traversal, geometry, JSON, queue |
| Saved/live capture context, source loading, working-database restoration | `capture_commands.*` | Placement policy, geometry repair |
| Legacy diagnostic command | `probe_command.cpp` | Product import |
| Geometry/coverage value types and wire metadata | `geometry_types.h`, `snapshot_writer.h` | AutoCAD object ownership |
| Region and hatch extraction | `region_extraction.cpp`, `hatch_extraction.cpp`, `hatch_loop_roles.*`, `surface_extraction.h` | Source file resolution, transport |
| Curves and primitive boundaries | `curve_extraction.*`, `curve_sampling.*`, `geometry_math.*` | Layer classification, transport |
| Exact two-polyline regions and pending near-closed-area proposals | `exact_polyline_pairs.*` | Source edits, automatic endpoint repair, layer mapping, polygonization |
| Database traversal and instance identity | `drawing_traversal.*`, `block_traversal.cpp`, `entity_extraction.cpp` | Snapshot publication |
| External database lifetime, dependency evidence and existing relinking policy | `xref_resolver.*`, `reference_search.*` | Choosing ambiguous source variants silently |
| Snapshot JSON and atomic publication | `snapshot_writer.cpp`, `file_io.*` | Autodesk APIs, input CAD parsing |
| Optional cancellation/progress callbacks | `operation_control.*` | MCP protocol, editor dialogs |
| MCP filesystem queue and request lifecycle | `mcp_transport.*` | Geometry interpretation |
| User-facing delivery workflow and dialogs | `delivery_command.*`, `delivery_recovery.*`, `delivery_ui.*` | Native geometry implementation |

Every production `.cpp` is compiled separately by `../build-macos.sh`. Do not
include `.cpp` or `.inc` implementation fragments. Native source/header files
are limited to 500 physical lines by `test_native_modules.py`.

## Lifetimes

AutoCAD database access remains synchronous on its main thread. The traversal
owns its external database cache for the entire capture. The walker closes each
opened entity after its block/leaf handler returns. Handlers do not close the
entity passed by the walker. Block helpers still close attributes they open.

Each capture returns `ExportResult` explicitly; no cross-request "last export"
global is used. An isolated source database restores the previous working
database before destruction. `ScopedOperationControl` restores the previous
callbacks on scope exit, including exception unwinding. The thread-local callback
storage does not make Autodesk database operations thread-safe.

## Checks

From the repository root on macOS:

```sh
apps/api/.venv/bin/python -m pytest tools/autocad-bridge/native/test_native_modules.py tools/autocad-bridge/mcp/test_green_atlas_autocad_mcp.py -q
GREEN_ATLAS_GEOMETRY_SELFTEST=1 bash tools/autocad-bridge/build-macos.sh
```

The portable runtime tests compile the actual snapshot writer, math, IO and
operation-control modules **without ObjectARX**. It checks holes, paths, points,
instance identity, XREF evidence, unresolved coverage, UTF-8/JSON escaping,
numeric precision, file/live provenance, cancellation at seven payload sections,
atomic replacement and callback restoration. A separate portable C++ check
exercises disjoint HATCH patches, nested island styles, overlapping loops,
cancellation and open-loop rejection. On non-macOS the snapshot executable skips
because the production hash implementation uses CommonCrypto; structural checks
still run. This test does not claim that Autodesk extracted those objects.

The qualification build retains `GAGEOMETRYSELFTEST` (including a new disjoint
HATCH control).
A successful link is not a successful in-AutoCAD run. Loading/installing a new
bundle and street-by-street identity comparison are separate qualification steps.
The builder produces a native candidate, not an installable complete desktop
product unless a qualified desktop application is explicitly supplied.

## Preserved limits and known gaps

- Multi-face REGION and typed disjoint HATCH loops passed real native capture
  on two streets; this does not imply that every HATCH or building outline is
  an admitted area. Non-associative edge-loop HATCH can still remain unresolved.
- Sampling bounds and tolerances are unchanged. The REGION loop walker
  reverses individual native edge samples only when adjacent Autodesk endpoints
  prove the direction. A native edge shorter than tolerance is oriented by an
  exact zero-gap endpoint only; genuinely ambiguous joins remain unresolved.
  It never reorders edges or closes a gap. HATCH edge-loop failure now retains
  the attempted `getRegionArea` stage/status. No new repair or alternate parser
  was introduced.
- File-based XREF search still has the package-root/unloaded-state limitations
  recorded in the three-street audit.
- `GAOPEN` still serializes/reloads a DXF copy and resolves/binds references;
  direct live snapshot capture exists separately. This refactor does not merge
  these user paths.
- Service `AUTOCAD_LIVE` projects are explicitly rejected by
  `apps/api/app/exporting/application.py::_source_components`. Their stored JSON
  snapshot is not an archived source DXF. Removing that guard alone is wrong.
- A single product handoff must eventually bind the geometry snapshot, archival
  CAD source and placement changes to one capture identity, then support native
  output and reopen verification. The archive should not become a second input
  parser for geometry.

The disabled unsafe edge-pointer HATCH extractor and its unused 2D sampler were
removed from the compilation source. Their history is recoverable in Git. The
active typed HATCH extractor remains in `hatch_extraction.cpp`; this is a move,
not a claim that all HATCH are supported.
