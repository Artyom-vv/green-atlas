#include "surface_extraction.h"
#include "curve_extraction.h"
#include "hatch_loop_roles.h"
#include "cad_utils.h"
#include "bridge_config.h"
#include "operation_control.h"
#include "dbhatch.h"
#include "dbregion.h"
#include "dbpl.h"
#include <cmath>
#include <utility>

namespace ga::bridge {
namespace {

// The hatch's evaluated surface is the authority when the typed-loop
// containment heuristic cannot represent overlapping boundaries. Keep the
// normal typed path for unambiguous polyline loops and do not accept a mere
// getArea() number without traversable, closed native REGION topology.
bool extractEvaluatedHatchArea(const AcDbHatch* hatch,
                               const AcGeMatrix3d& transform,
                               double tolerance,
                               RegionTopology& result,
                               std::string& failure) {
    AcDbRegion* areaRegion = hatch->getRegionArea();
    if (areaRegion == nullptr) {
        failure = "getRegionArea_returned_null";
        return false;
    }
    RegionTopology areaTopology;
    areaTopology.measurement.handle = result.measurement.handle;
    areaTopology.measurement.layer = result.measurement.layer;
    areaTopology.sourceLayer = result.sourceLayer;
    areaTopology.instanceChain = result.instanceChain;
    const bool extracted = extractRegionTopology(
        areaRegion, transform, tolerance, areaTopology);
    delete areaRegion;
    if (extracted && areaTopology.resolved && !areaTopology.loops.empty()) {
        areaTopology.extractionMethod = "autodesk-hatch-evaluated-region";
        result = std::move(areaTopology);
        failure.clear();
        return true;
    }
    result.errorStatus = areaTopology.errorStatus;
    failure = "getRegionArea_topology_status=" +
        std::to_string(areaTopology.errorStatus) + ", sampled_loops=" +
        std::to_string(areaTopology.loops.size());
    return false;
}

}  // namespace

// Use only the typed value-array overload. The edge-pointer overload entered
// AutoCAD's fatal handler on real elliptical loops and remains quarantined.
bool extractPolylineHatchTopology(const AcDbHatch* hatch,
                                  const AcGeMatrix3d& transform,
                                  const double tolerance,
                                  RegionTopology& result,
                                  std::string& reason) {
    const int loopCount = hatch->numLoops();
    if (loopCount < 1 || static_cast<std::size_t>(loopCount) >
            kMaximumTopologyElementsPerEntity) {
        reason = "HATCH has an invalid or oversized loop count";
        return false;
    }
    bool containsEdgeLoop = false;
    for (int loopIndex = 0; loopIndex < loopCount; ++loopIndex) {
        if (!(hatch->loopTypeAt(loopIndex) & AcDbHatch::kPolyline)) {
            containsEdgeLoop = true;
            break;
        }
    }
    std::string areaAttemptFailure;
    if (containsEdgeLoop) {
        // Ask AutoCAD's own geometry kernel for the evaluated hatch area. This
        // is a typed, owned Region result and safely covers non-associative
        // line/arc/ellipse/spline edge loops. Do not use the legacy
        // untyped edge-pointer overload: malformed real-world elliptic loops
        // can enter AutoCAD's fatal handler before callers can validate them.
        if (extractEvaluatedHatchArea(hatch, transform, tolerance,
                                      result, areaAttemptFailure)) {
            reason.clear();
            return true;
        }
    }
    for (int loopIndex = 0; loopIndex < loopCount; ++loopIndex) {
        if (operationCancelled()) return false;
        const auto type = hatch->loopTypeAt(loopIndex);
        NativePath sampled;
        if (!(type & AcDbHatch::kPolyline)) {
            AcDbObjectIdArray associatedObjects;
            const Acad::ErrorStatus associationStatus =
                hatch->getAssocObjIdsAt(loopIndex, associatedObjects);
            if (associationStatus != Acad::eOk ||
                associatedObjects.length() != 1) {
                reason = "HATCH edge-curve loop has no single associative "
                         "boundary object (loop=" +
                         std::to_string(loopIndex) + ", type=" +
                         std::to_string(static_cast<long long>(type)) +
                         ", associative=" +
                         std::string(hatch->associative() ? "true" : "false") +
                         ", boundary_objects=" +
                         std::to_string(
                             associationStatus == Acad::eOk
                                 ? associatedObjects.length()
                                 : 0) +
                         ", association_status=" +
                         std::to_string(static_cast<int>(associationStatus)) +
                         "); " + areaAttemptFailure;
                return false;
            }
            AcDbEntity* boundaryEntity = nullptr;
            const Acad::ErrorStatus openStatus = acdbOpenObject(
                boundaryEntity, associatedObjects[0], AcDb::kForRead);
            if (openStatus == Acad::eOk && boundaryEntity != nullptr) {
                if (AcDbRegion* boundaryRegion =
                        AcDbRegion::cast(boundaryEntity)) {
                    RegionTopology boundaryTopology;
                    const bool extracted = extractRegionTopology(
                        boundaryRegion, transform, tolerance, boundaryTopology);
                    boundaryEntity->close();
                    if (!extracted || boundaryTopology.loops.empty()) {
                        reason = "HATCH associative AcDbRegion boundary could "
                                 "not be traversed safely";
                        return false;
                    }
                    for (RegionLoop& regionLoop : boundaryTopology.loops) {
                        const double area = hatchLoopArea(regionLoop.coordinates);
                        if (!std::isfinite(area) ||
                            area <= tolerance * tolerance) {
                            reason = "HATCH associative AcDbRegion boundary has "
                                     "no usable projected area";
                            return false;
                        }
                        result.loops.push_back(std::move(regionLoop));
                    }
                    continue;
                }
            }
            const AcDbCurve* boundaryCurve =
                openStatus == Acad::eOk && boundaryEntity != nullptr
                    ? AcDbCurve::cast(boundaryEntity)
                    : nullptr;
            const bool extracted = boundaryCurve != nullptr &&
                extractDatabaseCurve(
                    boundaryCurve, transform, tolerance, sampled) &&
                sampled.resolved && sampled.closed;
            const std::string boundaryType = boundaryEntity == nullptr
                ? "unavailable"
                : utf8(boundaryEntity->isA()->name());
            if (boundaryEntity != nullptr) boundaryEntity->close();
            if (!extracted) {
                reason = "HATCH associative boundary could not be extracted "
                         "as one closed curve (type=" + boundaryType +
                         ", open_status=" +
                         std::to_string(static_cast<int>(openStatus)) +
                         ", detail=" + sampled.reason + ")";
                return false;
            }
        } else {
            if (type & (AcDbHatch::kNotClosed | AcDbHatch::kSelfIntersecting)) {
                reason = "AutoCAD marks the HATCH boundary open or self-intersecting";
                return false;
            }
            Adesk::Int32 returnedType = 0;
            AcGePoint2dArray vertices;
            AcGeDoubleArray bulges;
            const auto status = hatch->getLoopAt(
                loopIndex, returnedType, vertices, bulges);
            if (status != Acad::eOk || vertices.length() < 2 ||
                vertices.length() > kMaximumTopologyElementsPerEntity ||
                (!bulges.isEmpty() && bulges.length() != vertices.length())) {
                result.errorStatus = static_cast<int>(
                    status == Acad::eOk ? Acad::eInvalidInput : status);
                reason = "AutoCAD returned an invalid or oversized HATCH polyline boundary";
                return false;
            }
            AcDbPolyline boundary(vertices.length());
            if (boundary.setNormal(hatch->normal()) != Acad::eOk ||
                !std::isfinite(hatch->elevation())) {
                reason = "AutoCAD could not reproduce the HATCH boundary plane";
                return false;
            }
            boundary.setElevation(hatch->elevation());
            for (unsigned int index = 0; index < vertices.length(); ++index) {
                const double bulge = bulges.isEmpty() ? 0 : bulges[index];
                if (!std::isfinite(vertices[index].x) ||
                    !std::isfinite(vertices[index].y) || !std::isfinite(bulge) ||
                    boundary.addVertexAt(index, vertices[index], bulge) != Acad::eOk) {
                    reason = "HATCH polyline has invalid vertex values";
                    return false;
                }
            }
            boundary.setClosed(true);
            if (!extractLightweightPolyline(
                    &boundary, transform, tolerance, sampled) || !sampled.closed) {
                result.errorStatus = sampled.errorStatus;
                reason = "HATCH polyline could not be sampled as a closed boundary: " +
                         sampled.reason;
                return false;
            }
        }
        const double area = hatchLoopArea(sampled.coordinates);
        if (!std::isfinite(area) || area <= tolerance * tolerance) {
            reason = "HATCH polyline boundary has no usable projected area";
            return false;
        }
        RegionLoop loop;
        loop.coordinates = std::move(sampled.coordinates);
        loop.sampledMaximumDeviation = sampled.sampledMaximumDeviation;
        result.loops.push_back(std::move(loop));
    }
    const auto style = hatch->hatchStyle();
    HatchFillStyle fillStyle;
    if (style == AcDbHatch::kNormal) fillStyle = HatchFillStyle::Normal;
    else if (style == AcDbHatch::kOuter) fillStyle = HatchFillStyle::Outer;
    else if (style == AcDbHatch::kIgnore) fillStyle = HatchFillStyle::Ignore;
    else {
        reason = "HATCH has an unsupported fill style";
        result.loops.clear();
        return false;
    }
    double area = 0.0;
    double perimeter = 0.0;
    if (!classifyHatchLoops(result.loops, fillStyle, tolerance, area,
                            perimeter, reason, operationCancelled)) {
        result.loops.clear();
        if (reason == "HATCH loops overlap without a complete containment" &&
            !operationCancelled()) {
            const std::string classificationFailure = reason;
            if (extractEvaluatedHatchArea(hatch, transform, tolerance,
                                          result, areaAttemptFailure)) {
                reason.clear();
                return true;
            }
            reason = classificationFailure + "; " + areaAttemptFailure;
        }
        return false;
    }
    result.measurement.area = area;
    result.measurement.perimeter = perimeter;
    result.measurement.areaValid = true;
    result.measurement.perimeterValid = true;
    result.resolved = true;
    result.errorStatus = 0;
    result.extractionMethod = "autodesk-hatch-polyline-values";
    return true;
}

}  // namespace ga::bridge
