// Developer-only, dataset-driven native geometry experiment. No product route.
// Reads an isolated DWG and explicit handles. Never opens source entities for
// write, changes source coordinates, or saves the source DB. The closure-only
// variant deliberately modifies transient clones to test hypothetical chords.
#include "rxregsvc.h"
#include "core_rxmfcapi.h"
#include "aced.h"
#include "adslib.h"
#include "dbapserv.h"
#include "dbregion.h"
#include "dbcurve.h"
#include "dbpl.h"
#ifdef GA_CLOSURE_PROBE
#include "dbsymtb.h"
#include "gelnsg2d.h"
#include "gearc2d.h"
#include "geblok2d.h"
#include "gecint2d.h"
#ifdef GA_PRODUCTION_PROJECTION_PROBE
#include "surface_extraction.h"
#include "bridge_config.h"
#include "cad_utils.h"
#include "gemat3d.h"
#endif
#endif
#include "dbhatch.h"
#include "brbrep.h"
#include "brbftrav.h"
#include "brface.h"
#include "AcString.h"
#include "file_io.h"
#ifdef GA_DISTANCE_PROBE
#include "distance_probe.h"
#endif
#ifdef GA_TOPOLOGY_PROBE
#include "topology_probe.h"
#endif
#include <algorithm>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <limits>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <vector>

namespace {
using ga::bridge::jsonEscape;
#if defined(GA_DISTANCE_PROBE)
constexpr auto kCommandGroup = _T("GA_NATIVE_DISTANCE_DIAGNOSTIC");
constexpr auto kCommandName = _T("GADISTFILE");
#elif defined(GA_CLOSURE_PROBE)
constexpr auto kCommandGroup = _T("GA_NATIVE_CLOSURE_DIAGNOSTIC");
constexpr auto kCommandName = _T("GACLOSEFILE");
#elif defined(GA_HATCH_PROBE)
constexpr auto kCommandGroup = _T("GA_NATIVE_HATCH_DIAGNOSTIC");
constexpr auto kCommandName = _T("GAHATCHFILE");
#elif defined(GA_TOPOLOGY_PROBE)
#ifdef GA_SEAM_PROBE
constexpr auto kCommandGroup = _T("GA_NATIVE_SEAM_DIAGNOSTIC");
constexpr auto kCommandName = _T("GASEAMFILE");
#else
constexpr auto kCommandGroup = _T("GA_NATIVE_TOPOLOGY_DIAGNOSTIC");
constexpr auto kCommandName = _T("GATOPOFILE");
#endif
#else
constexpr auto kCommandGroup = _T("GA_NATIVE_CONTAINMENT_DIAGNOSTIC");
constexpr auto kCommandName = _T("GACONTAINFILE");
#endif
constexpr int kMaximumHandles = 256;
constexpr int kMaximumPoints = 64;
constexpr int kMaximumCases = 32;

std::string quote(const std::string& text) { return "\"" + jsonEscape(text) + "\""; }
struct Case {
    std::string name;
    std::vector<std::string> handles;
    std::vector<AcGePoint3d> points;
};

// Explicit line format, documented in README. It is not a CAD parser.
std::string line(std::istream& input) {
    std::string value;
    if (!std::getline(input, value)) throw std::runtime_error("truncated request");
    if (!value.empty() && value.back() == '\r') value.pop_back();
    return value;
}
int count(std::istream& input, int limit) {
    const auto value = line(input);
    std::size_t end = 0;
    int n = std::stoi(value, &end);
    if (end != value.size() || n < 1 || n > limit)
        throw std::runtime_error("invalid request count");
    return n;
}
void pointJson(std::ostream& out, const AcGePoint3d& point) {
    out << '[' << point.x << ',' << point.y << ',' << point.z << ']';
}
struct OpenedEntities {
    std::vector<AcDbEntity*> values;
    ~OpenedEntities() { for (auto* entity : values) entity->close(); }
};
struct Regions {
    std::vector<AcDbRegion*> values;
    bool owns = false;
    ~Regions() { if (owns) for (auto* region : values) delete region; }
};
const char* containmentName(AcGe::PointContainment value) {
    switch (value) {
    case AcGe::kInside: return "inside";
    case AcGe::kOutside: return "outside";
    case AcGe::kOnBoundary: return "on_boundary";
    default: return "unrecognized_enum";
    }
}
#ifdef GA_CLOSURE_PROBE
// Diagnostic only: intersect the authored line/arc segments with AcGe itself
// in OCS, not a sampled JSON projection or fabricated polygon.
void emitNativeSegmentIntersections(std::ostream& out, AcDbPolyline* polyline) {
    const unsigned int vertices = polyline->numVerts();
    out << ",\"native_vertices\":" << vertices
        << ",\"native_only_lines\":" << (polyline->isOnlyLines() ? "true" : "false")
        << ",\"native_has_bulges\":" << (polyline->hasBulges() ? "true" : "false");
    constexpr unsigned int kMaximumSegments = 5000;
    if (vertices < 2 || vertices > kMaximumSegments) {
        out << ",\"native_intersections_status\":\"not_tested\"";
        return;
    }
    struct Segment {
        unsigned int index;
        std::unique_ptr<AcGeCurve2d> curve;
        AcGePoint2d start, end, low, high;
    };
    std::vector<Segment> segments;
    segments.reserve(vertices - 1);
    unsigned int lineSegments = 0, arcSegments = 0, degenerateSegments = 0;
    for (unsigned int i = 0; i + 1 < vertices; ++i) {
        const auto type = polyline->segType(i);
        if (type != AcDbPolyline::kLine && type != AcDbPolyline::kArc) {
            ++degenerateSegments;
            continue;
        }
        std::unique_ptr<AcGeCurve2d> curve;
        if (type == AcDbPolyline::kLine) {
            auto segment = std::make_unique<AcGeLineSeg2d>();
            if (polyline->getLineSegAt(i, *segment) == Acad::eOk)
                curve = std::move(segment);
            ++lineSegments;
        } else {
            auto segment = std::make_unique<AcGeCircArc2d>();
            if (polyline->getArcSegAt(i, *segment) == Acad::eOk)
                curve = std::move(segment);
            ++arcSegments;
        }
        AcGePoint2d start, end;
        if (!curve || polyline->getPointAt(i, start) != Acad::eOk ||
            polyline->getPointAt(i + 1, end) != Acad::eOk) {
            out << ",\"native_intersections_status\":\"segment_query_failed\"";
            return;
        }
        AcGePoint2d low, high;
        curve->orthoBoundBlock().getMinMaxPoints(low, high);
        segments.push_back({i, std::move(curve), start, end, low, high});
    }
    out << ",\"native_line_segments\":" << lineSegments
        << ",\"native_arc_segments\":" << arcSegments
        << ",\"native_degenerate_segments\":" << degenerateSegments
        << ",\"native_intersections_status\":\"checked_line_and_arc_segments\""
        << ",\"native_nonadjacent_intersections\":[";
    bool first = true;
    constexpr unsigned int kMaximumReportedIntersections = 8;
    unsigned int reported = 0;
    for (std::size_t a = 0; a < segments.size() && reported < kMaximumReportedIntersections; ++a) {
        for (std::size_t b = a + 1; b < segments.size() && reported < kMaximumReportedIntersections; ++b) {
            if (segments[b].index <= segments[a].index + 1) continue;
            if (segments[a].high.x < segments[b].low.x ||
                segments[b].high.x < segments[a].low.x ||
                segments[a].high.y < segments[b].low.y ||
                segments[b].high.y < segments[a].low.y) continue;
            AcGeCurveCurveInt2d intersections(*segments[a].curve, *segments[b].curve);
            for (int n = 0; n < intersections.numIntPoints() &&
                            reported < kMaximumReportedIntersections; ++n) {
                const AcGePoint2d point = intersections.intPoint(n);
                constexpr double kEndpointTolerance = 1e-8;
                const bool onFirstEndpoint =
                    point.distanceTo(segments[a].start) <= kEndpointTolerance ||
                    point.distanceTo(segments[a].end) <= kEndpointTolerance;
                const bool onSecondEndpoint =
                    point.distanceTo(segments[b].start) <= kEndpointTolerance ||
                    point.distanceTo(segments[b].end) <= kEndpointTolerance;
                // First/last segments of an endpoint-coincident open polyline
                // share the intentional ring seam; it is not a self-crossing.
                if (segments[a].index == 0 && segments[b].index + 1 == vertices - 1 &&
                    onFirstEndpoint && onSecondEndpoint &&
                    segments[a].start.distanceTo(segments[b].end) <= kEndpointTolerance)
                    continue;
                if (!first) out << ',';
                first = false;
                out << "{\"first_segment\":" << segments[a].index
                    << ",\"second_segment\":" << segments[b].index
                    << ",\"first_type\":"
                    << quote(polyline->segType(segments[a].index) == AcDbPolyline::kLine ? "line" : "arc")
                    << ",\"second_type\":"
                    << quote(polyline->segType(segments[b].index) == AcDbPolyline::kLine ? "line" : "arc")
                    << ",\"ocs_xy\":[" << point.x << ',' << point.y << ']'
                    << ",\"first_start\":[" << segments[a].start.x << ',' << segments[a].start.y << ']'
                    << ",\"first_end\":[" << segments[a].end.x << ',' << segments[a].end.y << ']'
                    << ",\"second_start\":[" << segments[b].start.x << ',' << segments[b].start.y << ']'
                    << ",\"second_end\":[" << segments[b].end.x << ',' << segments[b].end.y << ']'
                    << ",\"at_first_endpoint\":" << (onFirstEndpoint ? "true" : "false")
                    << ",\"at_second_endpoint\":" << (onSecondEndpoint ? "true" : "false")
                    << ",\"transversal\":" << (intersections.isTransversal(n) ? "true" : "false")
                    << '}';
                ++reported;
            }
        }
    }
    out << "],\"native_intersections_truncated\":"
        << (reported == kMaximumReportedIntersections ? "true" : "false");
}
#endif
void emitEntity(std::ostream& out, const std::string& handle, AcDbEntity* entity) {
    out << "{\"handle\":" << quote(handle)
        << ",\"class\":" << quote(AcString(entity->isA()->name()).utf8Str())
        << ",\"layer\":" << quote(AcString(entity->layer()).utf8Str());
    if (auto* curve = AcDbCurve::cast(entity)) {
        AcGePoint3d first, last;
        auto a = curve->getStartPoint(first), b = curve->getEndPoint(last);
        double measuredArea = 0.0;
        const auto areaStatus = curve->getArea(measuredArea);
        out << ",\"closed\":" << (curve->isClosed() ? "true" : "false")
            << ",\"start_status\":" << int(a) << ",\"end_status\":" << int(b)
            << ",\"curve_area_status\":" << int(areaStatus)
            << ",\"curve_area_with_implicit_chord_units2\":";
        if (areaStatus == Acad::eOk && std::isfinite(measuredArea))
            out << measuredArea;
        else
            out << "null";
        if (a == Acad::eOk && b == Acad::eOk) {
            out << ",\"start\":"; pointJson(out, first);
            out << ",\"end\":"; pointJson(out, last);
            out << ",\"endpoint_gap_units\":" << first.distanceTo(last);
        }
    }
#ifdef GA_CLOSURE_PROBE
    if (auto* polyline = AcDbPolyline::cast(entity))
        emitNativeSegmentIntersections(out, polyline);
#endif
#ifdef GA_HATCH_PROBE
    if (auto* hatch = AcDbHatch::cast(entity)) {
        const int loopCount = hatch->numLoops();
        out << ",\"hatch_style\":" << int(hatch->hatchStyle())
            << ",\"loop_count\":" << loopCount << ",\"loops\":[";
        constexpr int kMaximumReportedLoops = 64;
        const int reported = std::min(loopCount, kMaximumReportedLoops);
        for (int index = 0; index < reported; ++index) {
            if (index) out << ',';
            const auto type = hatch->loopTypeAt(index);
            out << "{\"index\":" << index << ",\"type\":" << type
                << ",\"polyline\":"
                << ((type & AcDbHatch::kPolyline) ? "true" : "false");
            if (type & AcDbHatch::kPolyline) {
                Adesk::Int32 returnedType = 0;
                AcGePoint2dArray vertices;
                AcGeDoubleArray bulges;
                const auto status = hatch->getLoopAt(
                    index, returnedType, vertices, bulges);
                out << ",\"typed_status\":" << int(status)
                    << ",\"returned_type\":" << returnedType;
                if (status == Acad::eOk) {
                    out << ",\"vertices\":" << vertices.length()
                        << ",\"bulges\":" << bulges.length();
                    if (!vertices.isEmpty()) {
                        double minX = vertices[0].x, minY = vertices[0].y;
                        double maxX = minX, maxY = minY;
                        for (int vertex = 1; vertex < vertices.length(); ++vertex) {
                            minX = std::min(minX, vertices[vertex].x);
                            minY = std::min(minY, vertices[vertex].y);
                            maxX = std::max(maxX, vertices[vertex].x);
                            maxY = std::max(maxY, vertices[vertex].y);
                        }
                        out << ",\"ocs_bounds\":[" << minX << ',' << minY
                            << ',' << maxX << ',' << maxY << ']';
                    }
                }
            }
            out << '}';
        }
        out << "],\"loops_truncated\":"
            << (loopCount > reported ? "true" : "false");
    }
#endif
    out << '}';
}

#ifdef GA_CLOSURE_PROBE
// The clone is appended only to the isolated side database in memory and
// reopened FOR READ before createFromCurves. Autodesk documents a crash risk
// for curves still open for write.
void convertTemporaryClone(AcDbDatabase& database,
                           std::unique_ptr<AcDbPolyline>& clone,
                           Regions& regions, Acad::ErrorStatus& conversion) {
    AcDbBlockTable* table = nullptr;
    auto status = database.getBlockTable(table, AcDb::kForRead);
    if (status != Acad::eOk || !table)
        throw std::runtime_error("Temporary database block table unavailable");
    AcDbBlockTableRecord* model = nullptr;
    status = table->getAt(ACDB_MODEL_SPACE, model, AcDb::kForWrite);
    table->close();
    if (status != Acad::eOk || !model)
        throw std::runtime_error("Temporary model space not writable");
    AcDbObjectId cloneId;
    status = model->appendAcDbEntity(cloneId, clone.get());
    model->close();
    if (status != Acad::eOk)
        throw std::runtime_error("Could not append temporary closed clone");
    clone.release()->close();

    AcDbPolyline* readClone = nullptr;
    status = acdbOpenObject(readClone, cloneId, AcDb::kForRead);
    if (status != Acad::eOk || !readClone)
        throw std::runtime_error("Temporary closed clone not read-openable");
    if (!readClone->isReadEnabled() || readClone->isWriteEnabled()) {
        readClone->close();
        throw std::runtime_error("Temporary clone is not read-only");
    }
    AcArray<AcDbEntity*> curves;
    curves.append(readClone);
    AcArray<AcDbRegion*> created;
    conversion = AcDbRegion::createFromCurves(curves, created);
    readClone->close();
    regions.owns = true;
    for (int i = 0; i < created.length(); ++i)
        regions.values.push_back(created[i]);
}

#ifdef GA_PRODUCTION_PROJECTION_PROBE
void emitProductRegionProjection(std::ostream& out, AcDbDatabase& database,
                                 AcDbRegion* region) {
    const double metresPerUnit = ga::bridge::unitToMetres(database.insunits());
    if (metresPerUnit <= 0.0) return;
    AcGeVector3d normal;
    const auto normalStatus = region->getNormal(normal);
    ga::bridge::RegionTopology projection;
    const bool projected = ga::bridge::extractRegionTopology(
        region, AcGeMatrix3d::kIdentity,
        ga::bridge::kRequestedToleranceMetres / metresPerUnit,
        projection);
    out << ",\"product_extraction\":{\"resolved\":"
        << (projected && projection.resolved ? "true" : "false")
        << ",\"error_status\":" << projection.errorStatus
        << ",\"native_normal_status\":" << int(normalStatus)
        << ",\"area_units2\":" << projection.measurement.area
        << ",\"perimeter_units\":" << projection.measurement.perimeter
        << ",\"loops\":[";
    for (std::size_t loopIndex = 0; loopIndex < projection.loops.size(); ++loopIndex) {
        if (loopIndex) out << ',';
        const auto& loop = projection.loops[loopIndex];
        out << "{\"role\":" << quote(loop.role)
            << ",\"sampled_maximum_deviation_units\":"
            << loop.sampledMaximumDeviation << ",\"coordinates\":[";
        for (std::size_t pointIndex = 0; pointIndex < loop.coordinates.size(); ++pointIndex) {
            if (pointIndex) out << ',';
            const auto& point = loop.coordinates[pointIndex];
            out << '[' << point.x << ',' << point.y << ',' << point.z << ']';
        }
        out << "]}";
    }
    out << "]}";
}
#endif

// One narrow hypothesis: an authored line ends on its own first three
// straight segments, leaving a short initial spur. Trimming that spur is
// tested on a transient clone only; a successful area is still only a review
// candidate, never a silent production interpretation of the source.
void tryInitialSpurCandidate(AcDbDatabase& database, AcDbPolyline* source,
                             std::ostream& out) {
    out << ",\"initial_spur_candidate\":{";
    const auto count = source->numVerts();
    AcGePoint2d last;
    if (count < 4 || source->getPointAt(count - 1, last) != Acad::eOk) {
        out << "\"status\":\"not_applicable\"}";
        return;
    }
    AcGePoint2d first;
    if (source->getPointAt(0, first) != Acad::eOk ||
        first.distanceTo(last) <= 1e-8) {
        out << "\"status\":\"coincident_endpoints_or_unavailable\"}";
        return;
    }
    int initialSegment = -1;
    double initialParameter = 0.0;
    out << "\"initial_segment_checks\":[";
    bool firstCheck = true;
    for (unsigned int i = 0; i < std::min(count - 2, 3U); ++i) {
        if (!firstCheck) out << ',';
        firstCheck = false;
        const auto type = source->segType(i);
        out << "{\"index\":" << i << ",\"type\":"
            << quote(type == AcDbPolyline::kLine ? "line" :
                     type == AcDbPolyline::kArc ? "arc" : "other");
        if (type != AcDbPolyline::kLine) { out << '}'; continue; }
        double parameter = 0.0;
        const bool onSegment = source->onSegAt(i, last, parameter);
        out << ",\"on_segment\":" << (onSegment ? "true" : "false")
            << ",\"polyline_parameter\":" << parameter
            << ",\"segment_fraction\":" << parameter - i << '}';
        if (onSegment &&
            parameter - i > 1e-8 && parameter - i < 1.0 - 1e-8) {
            initialSegment = int(i);
            initialParameter = parameter - i;
            break;
        }
    }
    out << "],";
    if (initialSegment < 0) {
        out << "\"status\":\"end_not_on_initial_straight_segment\"}";
        return;
    }
    out << "\"status\":\"native_candidate\",\"initial_segment\":"
        << initialSegment << ",\"segment_parameter\":" << initialParameter;
    std::unique_ptr<AcDbPolyline> candidate(AcDbPolyline::cast(source->clone()));
    if (!candidate) throw std::runtime_error("Initial spur clone failed");
    for (int index = 0; index < initialSegment; ++index) {
        const auto status = candidate->removeVertexAt(0);
        if (status != Acad::eOk)
            throw std::runtime_error("Initial spur removal failed " + std::to_string(int(status)));
    }
    const auto setStatus = candidate->setPointAt(0, last);
    out << ",\"trim_start_status\":" << int(setStatus);
    if (setStatus != Acad::eOk) { out << '}'; return; }
    candidate->setClosed(Adesk::kTrue);
    out << ",\"clone_closed\":" << (candidate->isClosed() ? "true" : "false");
    if (!candidate->isClosed()) { out << '}'; return; }
    Regions regions;
    Acad::ErrorStatus conversion = Acad::eInvalidInput;
    convertTemporaryClone(database, candidate, regions, conversion);
    out << ",\"conversion_status\":" << int(conversion)
        << ",\"regions\":[";
    for (std::size_t i = 0; i < regions.values.size(); ++i) {
        if (i) out << ',';
        double area = 0.0;
        const auto areaStatus = regions.values[i]->getArea(area);
        AcBrBrep brep;
        const auto brepStatus = brep.set(*regions.values[i]);
        out << "{\"area_status\":" << int(areaStatus)
            << ",\"area_units2\":";
        if (areaStatus == Acad::eOk && std::isfinite(area)) out << area;
        else out << "null";
        out << ",\"brep_set_status\":" << int(brepStatus);
#ifdef GA_TOPOLOGY_PROBE
        if (brepStatus == AcBr::eOk) emitTopologyProbe(out, brep);
#endif
#ifdef GA_PRODUCTION_PROJECTION_PROBE
        if (brepStatus == AcBr::eOk)
            emitProductRegionProjection(out, database, regions.values[i]);
#endif
        out << '}';
    }
    out << "]}";
}

void tryClosedClone(AcDbDatabase& database, AcDbPolyline* source,
                    std::ostream& out, Regions& regions,
                    Acad::ErrorStatus& conversion) {
    std::unique_ptr<AcDbPolyline> clone(AcDbPolyline::cast(source->clone()));
    if (!clone) throw std::runtime_error("AcDbPolyline clone failed");
    AcGePoint3d first, last;
    if (source->getStartPoint(first) != Acad::eOk ||
        source->getEndPoint(last) != Acad::eOk)
        throw std::runtime_error("Polyline endpoint query failed");
    const double gap = first.distanceTo(last);
    if (!std::isfinite(gap)) throw std::runtime_error("Non-finite endpoint gap");
    const double tolerance = std::nextafter(
        gap * 1.01, std::numeric_limits<double>::infinity());
    const auto closeStatus =
        clone->makeClosedIfStartAndEndVertexCoincide(tolerance);
    out << ",\"requested_close_tolerance_units\":" << tolerance
        << ",\"clone_close_status\":" << int(closeStatus)
        << ",\"auto_clone_closed\":" << (clone->isClosed() ? "true" : "false");
    if (!clone->isClosed()) {
        clone->setClosed(Adesk::kTrue);
    }
    out << ",\"clone_closed_after_explicit_request\":"
        << (clone->isClosed() ? "true" : "false");
    if (clone->isClosed())
        convertTemporaryClone(database, clone, regions, conversion);
    if (conversion != Acad::eOk)
        tryInitialSpurCandidate(database, source, out);
}
#endif

void runCase(AcDbDatabase& database, const Case& request, std::ostream& out) {
    const auto started = std::chrono::steady_clock::now();
    OpenedEntities entities;
    Regions regions; // Destroy native temporary regions before closing curves.
    out << "{\"name\":" << quote(request.name) << ",\"entities\":[";
    bool allOpened = true;
    for (std::size_t i = 0; i < request.handles.size(); ++i) {
        if (i) out << ',';
        AcDbObjectId id;
        const AcString handle(request.handles[i].c_str());
        auto status = database.getAcDbObjectId(id, false, AcDbHandle(handle.kwszPtr()));
        AcDbEntity* entity = nullptr;
        if (status == Acad::eOk) status = acdbOpenObject(entity, id, AcDb::kForRead);
        if (status != Acad::eOk || !entity) {
            allOpened = false;
            out << "{\"handle\":" << quote(request.handles[i])
                << ",\"open_status\":" << int(status) << '}';
        } else {
            entities.values.push_back(entity);
            emitEntity(out, request.handles[i], entity);
        }
    }
    out << ']';
#ifdef GA_HATCH_PROBE
    // Inventory only: never call getRegionArea or the unsafe untyped
    // edge-pointer getLoopAt overload on real dataset hatches.
    out << ",\"method\":\"AcDbHatch.typedLoopInventory\",\"regions\":[]"
        << ",\"elapsed_ms\":"
        << std::chrono::duration<double, std::milli>(
               std::chrono::steady_clock::now() - started).count() << '}';
    return;
#endif
    Acad::ErrorStatus conversion = Acad::eInvalidInput;
    const char* method = "not_attempted";
#ifdef GA_CLOSURE_PROBE
    if (allOpened && entities.values.size() == 1 &&
        AcDbPolyline::cast(entities.values.front())) {
        method = "AcDbPolyline.clone+native_close+AcDbRegion.createFromCurves";
        tryClosedClone(database, AcDbPolyline::cast(entities.values.front()),
                       out, regions, conversion);
    } else
#endif
    if (allOpened && entities.values.size() == 1 &&
        AcDbRegion::cast(entities.values.front())) {
        method = "existing_region";
        regions.values.push_back(AcDbRegion::cast(entities.values.front()));
        conversion = Acad::eOk;
    } else if (allOpened && entities.values.size() == 1 &&
               AcDbHatch::cast(entities.values.front())) {
        method = "AcDbHatch.getRegionArea";
        regions.owns = true;
        auto* region = AcDbHatch::cast(entities.values.front())->getRegionArea();
        if (region) { regions.values.push_back(region); conversion = Acad::eOk; }
    } else if (allOpened) {
        method = "AcDbRegion.createFromCurves";
        AcArray<AcDbEntity*> curves;
        for (auto* entity : entities.values) {
            // Whitelist documented native types; no fallback or approximation.
            const std::string type = AcString(entity->isA()->name()).utf8Str();
            if (type != "AcDbLine" && type != "AcDbArc" && type != "AcDbEllipse" &&
                type != "AcDbCircle" && type != "AcDbSpline" &&
                type != "AcDb3dPolyline" && type != "AcDbPolyline") {
                throw std::runtime_error("unsupported curve input " + type);
            }
            curves.append(entity);
        }
        AcArray<AcDbRegion*> created;
        regions.owns = true;
        conversion = AcDbRegion::createFromCurves(curves, created);
        // The API may return partial regions even on failure. Preserve evidence.
        for (int i = 0; i < created.length(); ++i) regions.values.push_back(created[i]);
    }
    out << ",\"method\":" << quote(method)
        << ",\"conversion_status\":" << int(conversion)
        << ",\"regions\":[";
    for (std::size_t i = 0; i < regions.values.size(); ++i) {
        if (i) out << ',';
        double area = 0;
        const auto areaStatus = regions.values[i]->getArea(area);
        AcBrBrep brep;
        const auto setStatus = brep.set(*regions.values[i]);
        out << "{\"area_status\":" << int(areaStatus) << ",\"area_units2\":";
        if (areaStatus == Acad::eOk && std::isfinite(area)) out << area; else out << "null";
        out << ",\"brep_set_status\":" << int(setStatus) << ",\"queries\":[";
        for (std::size_t j = 0; j < request.points.size(); ++j) {
            if (j) out << ',';
            out << "{\"point\":"; pointJson(out, request.points[j]);
            AcGe::PointContainment value = AcGe::kOutside;
            AcBrEntity* container = nullptr;
            auto status = setStatus;
            if (status == AcBr::eOk)
                status = brep.getPointContainment(request.points[j], value, container);
            std::unique_ptr<AcBrEntity> owner(container);
            out << ",\"status\":" << int(status) << ",\"containment\":";
            if (status == AcBr::eOk) out << quote(containmentName(value)); else out << "null";
            out << ",\"container_class\":";
            if (container) out << quote(AcString(container->isA()->name()).utf8Str());
            else out << "null";
            out << '}';
        }
        out << ']';
#ifdef GA_DISTANCE_PROBE
        if (setStatus == AcBr::eOk) emitDistanceProbe(out, brep, request.points);
#endif
#ifdef GA_TOPOLOGY_PROBE
        if (setStatus == AcBr::eOk) emitTopologyProbe(out, brep);
#endif
#ifdef GA_PRODUCTION_PROJECTION_PROBE
        if (setStatus == AcBr::eOk)
            emitProductRegionProjection(out, database, regions.values[i]);
#endif
        out << '}';
    }
    out << "],\"elapsed_ms\":"
        << std::chrono::duration<double, std::milli>(
               std::chrono::steady_clock::now() - started).count() << '}';
}

void run() {
    ACHAR requestPath[4096] = {};
    if (acedGetString(1, _T("\nDiagnostic request path: "), requestPath) != RTNORM) return;
    try {
        std::ifstream request(AcString(requestPath).utf8Str());
        const std::string source = line(request), output = line(request);
        if (source.size() < 4 || (source.substr(source.size() - 4) != ".dwg" &&
                                  source.substr(source.size() - 4) != ".dxf"))
            throw std::runtime_error("DWG or DXF dataset input required");
        if (output.size() < 5 || output.substr(output.size() - 5) != ".json" ||
            std::ifstream(output).good()) throw std::runtime_error("output must be a NEW JSON file");
        std::vector<Case> cases;
        const int n = count(request, kMaximumCases);
        for (int i = 0; i < n; ++i) {
            Case item; item.name = line(request);
            const int handles = count(request, kMaximumHandles);
            for (int j = 0; j < handles; ++j) item.handles.push_back(line(request));
            const int points = count(request, kMaximumPoints);
            for (int j = 0; j < points; ++j) {
                std::istringstream row(line(request)); double x, y, z;
                if (!(row >> x >> y >> z) || !std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z))
                    throw std::runtime_error("invalid point");
                item.points.emplace_back(x, y, z);
            }
            cases.push_back(std::move(item));
        }
        auto* services = acdbHostApplicationServices();
        auto* previous = services->workingDatabase();
        AcDbDatabase database(false, true);
        struct Restore { AcDbHostApplicationServices* services; AcDbDatabase* previous;
            ~Restore() { services->setWorkingDatabase(previous); } } restore{services, previous};
        const auto before = ga::bridge::sha256File(source);
        if (before.empty()) throw std::runtime_error("source cannot be hashed");
        const bool isDxf = source.size() >= 4 && source.substr(source.size() - 4) == ".dxf";
        const auto opened = isDxf
            ? database.dxfIn(AcString(source.c_str()).kwszPtr())
            : database.readDwgFile(AcString(source.c_str()).kwszPtr(),
                                  AcDbDatabase::kForReadAndAllShare, true);
        if (opened != Acad::eOk) throw std::runtime_error("AutoCAD database open status " + std::to_string(int(opened)));
        services->setWorkingDatabase(&database);
        std::ostringstream out; out << std::setprecision(17);
        out << "{\"schema\":\"green-atlas.native-containment-experiment/1\","
            << "\"source\":" << quote(source) << ",\"source_sha256\":" << quote(before)
            << ",\"units_code\":" << int(database.insunits()) << ",\"cases\":[";
        for (std::size_t i = 0; i < cases.size(); ++i) {
            if (i) out << ',';
            std::ostringstream result; result << std::setprecision(17);
            try { runCase(database, cases[i], result); out << result.str(); }
            catch (const std::exception& error) {
                out << "{\"name\":" << quote(cases[i].name) << ",\"error\":" << quote(error.what()) << '}';
            }
        }
        out << "],\"source_unchanged\":"
            << (before == ga::bridge::sha256File(source) ? "true" : "false") << "}\n";
        if (!ga::bridge::writeAtomicText(output, out.str())) throw std::runtime_error("report publication failed");
        acutPrintf(_T("\nNative containment report: %s"), AcString(output.c_str()).kwszPtr());
    } catch (const std::exception& error) {
        acutPrintf(_T("\nNative containment diagnostic: %s"), AcString(error.what()).kwszPtr());
    }
}
} // namespace

extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode message, void* appId) {
    if (message == AcRx::kInitAppMsg) {
        acrxLoadModule(_T("AcGeomentObj.dbx"), 0);
        acrxDynamicLinker->loadModule(_T("AcBr.dbx"), 1);
        acrxDynamicLinker->unlockApplication(appId);
        acrxDynamicLinker->registerAppMDIAware(appId);
        acedRegCmds->addCommand(kCommandGroup, kCommandName,
                               kCommandName, ACRX_CMD_MODAL, run);
    } else if (message == AcRx::kUnloadAppMsg) {
        acedRegCmds->removeGroup(kCommandGroup);
        // Shared native dependencies may be in use by the production bridge.
    }
    return AcRx::kRetOK;
}
