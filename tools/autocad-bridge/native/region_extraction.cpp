#include "surface_extraction.h"
#include "curve_sampling.h"
#include "geometry_math.h"
#include "bridge_config.h"
#include "operation_control.h"
#include "dbregion.h"
#include "gemat3d.h"
#include "brbrep.h"
#include "brbftrav.h"
#include "brface.h"
#include "brfltrav.h"
#include "brloop.h"
#include "brletrav.h"
#include "gecurv3d.h"
#include <algorithm>
#include <cmath>

namespace ga::bridge {

namespace {
// AcBr returns loop edges in persistent order, but an oriented curve can
// still have descending traversal direction on this AutoCAD build. Use only
// the exact adjacent native endpoints to choose direction; never reorder
// edges or invent a gap-closing segment.
bool joinNativeLoopEdges(const std::vector<std::vector<Point3>>& edges,
                         double tolerance,
                         std::vector<Point3>& output) {
    if (edges.empty()) return false;
    for (bool reverseFirst : {false, true}) {
        std::vector<Point3> joined = edges.front();
        if (reverseFirst) std::reverse(joined.begin(), joined.end());
        bool contiguous = true;
        for (std::size_t index = 1; index < edges.size(); ++index) {
            const auto& edge = edges[index];
            if (edge.size() < 2 ||
                joined.size() + edge.size() - 1 > kMaximumSampledPointsPerLoop) {
                contiguous = false;
                break;
            }
            const double forwardGap = pointDistance(joined.back(), edge.front());
            const double reverseGap = pointDistance(joined.back(), edge.back());
            bool forward = forwardGap <= tolerance;
            bool reverse = reverseGap <= tolerance;
            // A real AcBr edge can be shorter than the sampling tolerance.
            // Both ends then appear close, although one is the exact shared
            // native vertex. Prefer only that exact endpoint; if neither (or
            // both) is exact, keep rejecting the ambiguous orientation.
            if (forward && reverse) {
                if (forwardGap == 0.0 && reverseGap > 0.0) reverse = false;
                else if (reverseGap == 0.0 && forwardGap > 0.0) forward = false;
            }
            if (forward == reverse) {
                contiguous = false;
                break;
            }
            if (forward) joined.insert(joined.end(), edge.begin() + 1, edge.end());
            else joined.insert(joined.end(), edge.rbegin() + 1, edge.rend());
        }
        if (contiguous && joined.size() >= 4 &&
            pointDistance(joined.front(), joined.back()) <= tolerance) {
            joined.back() = joined.front();
            output = std::move(joined);
            return true;
        }
    }
    return false;
}
}  // namespace

bool transformedAreaScale(AcDbRegion* region,
                          const AcGeMatrix3d& transform,
                          double& areaScale) {
    // Multi-face HATCH regions can expose valid AcBr areas while getNormal()
    // returns eInvalidInput. A similarity transform scales ANY face by s²;
    // it does not require a single region normal. Keep the normal-dependent
    // path for non-uniform transforms, where the supporting plane matters.
    if (transform.isUniScaledOrtho()) {
        AcGeVector3d axis = AcGeVector3d::kXAxis;
        axis.transformBy(transform);
        areaScale = axis.lengthSqrd();
        return std::isfinite(areaScale) && areaScale > 0.0;
    }
    AcGeVector3d normal;
    if (region->getNormal(normal) != Acad::eOk || normal.isZeroLength()) {
        return false;
    }
    normal.normalize();
    AcGeVector3d firstAxis = normal.perpVector();
    if (firstAxis.isZeroLength()) {
        return false;
    }
    firstAxis.normalize();
    AcGeVector3d secondAxis = normal.crossProduct(firstAxis);
    if (secondAxis.isZeroLength()) {
        return false;
    }
    secondAxis.normalize();
    firstAxis.transformBy(transform);
    secondAxis.transformBy(transform);
    areaScale = firstAxis.crossProduct(secondAxis).length();
    return std::isfinite(areaScale) && areaScale > 0.0;
}

bool extractRegionTopology(AcDbRegion* region,
                           const AcGeMatrix3d& transform,
                           const double tolerance,
                           RegionTopology& result) {
    AcBrBrep brep;
    AcBr::ErrorStatus status = brep.set(*region);
    if (status != AcBr::eOk) {
        result.errorStatus = static_cast<int>(status);
        return false;
    }

    double achievedAreaTolerance = 0.0;
    double achievedPerimeterTolerance = 0.0;
    const double requestedPropertyTolerance = 1e-10;
    double localArea = 0.0;
    double localPerimeter = 0.0;
    result.measurement.areaValid = brep.getSurfaceArea(
        localArea, requestedPropertyTolerance, achievedAreaTolerance) == AcBr::eOk;
    result.measurement.perimeterValid = brep.getPerimeterLength(
        localPerimeter, requestedPropertyTolerance,
        achievedPerimeterTolerance) == AcBr::eOk;
    if (!result.measurement.areaValid || !result.measurement.perimeterValid) {
        result.errorStatus = static_cast<int>(AcBr::eUnsuitableGeometry);
        return false;
    }
    double areaScale = 0.0;
    if (!transformedAreaScale(region, transform, areaScale)) {
        result.errorStatus = static_cast<int>(AcBr::eUnsuitableGeometry);
        return false;
    }
    result.measurement.area = localArea * areaScale;
    const bool uniformTransform = transform.isUniScaledOrtho();
    AcGeVector3d transformedUnit = AcGeVector3d::kXAxis;
    transformedUnit.transformBy(transform);
    const double uniformScale = transformedUnit.length();
    if (uniformTransform && std::isfinite(uniformScale) && uniformScale > 0.0) {
        result.measurement.perimeter = localPerimeter * uniformScale;
    } else {
        result.measurement.perimeter = 0.0;
    }

    AcBrBrepFaceTraverser faceTraverser;
    status = faceTraverser.setBrep(brep);
    if (status != AcBr::eOk) {
        result.errorStatus = static_cast<int>(status);
        return false;
    }

    std::size_t faceCount = 0;
    std::size_t totalLoopCount = 0;
    while (!faceTraverser.done() && status == AcBr::eOk) {
        if (operationCancelled()) return false;
        ++faceCount;
        // A native REGION may contain separate faces. Keep every Autodesk
        // exterior/interior loop; the snapshot consumer validates their
        // measured area and rejects intersecting or touching surfaces.
        if (faceCount > kMaximumTopologyElementsPerEntity) {
            result.errorStatus = static_cast<int>(AcBr::eUnsuitableTopology);
            return false;
        }
        AcBrFace face;
        status = faceTraverser.getFace(face);
        if (status != AcBr::eOk) break;

        AcBrFaceLoopTraverser loopTraverser;
        status = loopTraverser.setFace(face);
        if (status != AcBr::eOk) break;

        std::size_t exteriorLoopCount = 0;
        while (!loopTraverser.done() && status == AcBr::eOk) {
            if (operationCancelled()) return false;
            if (++totalLoopCount > kMaximumTopologyElementsPerEntity) {
                result.errorStatus = static_cast<int>(AcBr::eUnsuitableTopology);
                return false;
            }
            AcBrLoop loop;
            status = loopTraverser.getLoop(loop);
            if (status != AcBr::eOk) break;

            AcBr::LoopType loopType = AcBr::kLoopUnclassified;
            status = loop.getType(loopType);
            if (status != AcBr::eOk ||
                (loopType != AcBr::kLoopExterior && loopType != AcBr::kLoopInterior)) {
                result.errorStatus = status == AcBr::eOk
                    ? static_cast<int>(AcBr::eAmbiguousOutput)
                    : static_cast<int>(status);
                return false;
            }

            RegionLoop extractedLoop;
            extractedLoop.faceIndex = faceCount - 1;
            extractedLoop.role = loopType == AcBr::kLoopExterior ? "outer" : "hole";
            if (loopType == AcBr::kLoopExterior) ++exteriorLoopCount;
            AcBrLoopEdgeTraverser edgeTraverser;
            status = edgeTraverser.setLoop(loopTraverser);
            if (status != AcBr::eOk) break;

            std::size_t traversedEdges = 0;
            std::vector<std::vector<Point3>> edgeSamples;
            while (!edgeTraverser.done() && status == AcBr::eOk) {
                if (operationCancelled()) return false;
                if (++traversedEdges > kMaximumTopologyElementsPerEntity) {
                    result.errorStatus = static_cast<int>(AcBr::eUnsuitableTopology);
                    return false;
                }
                AcGeCurve3d* curve = nullptr;
                status = edgeTraverser.getOrientedCurve(curve);
                if (status != AcBr::eOk || curve == nullptr) {
                    delete curve;
                    break;
                }
                std::vector<Point3> samples;
                double edgeDeviation = 0.0;
                const bool sampled = appendSampledCurve(
                    *curve, transform, tolerance, samples, edgeDeviation);
                delete curve;
                if (!sampled) {
                    result.errorStatus = static_cast<int>(AcBr::eUnsuitableGeometry);
                    return false;
                }
                extractedLoop.sampledMaximumDeviation = std::max(
                    extractedLoop.sampledMaximumDeviation, edgeDeviation);
                edgeSamples.push_back(std::move(samples));
                status = edgeTraverser.next();
            }
            if (status != AcBr::eOk) break;
            if (!joinNativeLoopEdges(edgeSamples, tolerance,
                                     extractedLoop.coordinates)) {
                result.errorStatus = static_cast<int>(AcBr::eTopologyMismatch);
                return false;
            }
            if (!uniformTransform) {
                for (std::size_t pointIndex = 1;
                     pointIndex < extractedLoop.coordinates.size(); ++pointIndex) {
                    result.measurement.perimeter += pointDistance(
                        extractedLoop.coordinates[pointIndex - 1],
                        extractedLoop.coordinates[pointIndex]);
                }
            }
            result.loops.push_back(std::move(extractedLoop));

            status = loopTraverser.next();
        }
        if (status != AcBr::eOk) break;
        if (exteriorLoopCount != 1) {
            result.errorStatus = static_cast<int>(AcBr::eUnsuitableTopology);
            return false;
        }
        status = faceTraverser.next();
    }

    if (status != AcBr::eOk || faceCount == 0 || result.loops.empty()) {
        result.errorStatus = status == AcBr::eOk
            ? static_cast<int>(AcBr::eUnsuitableTopology)
            : static_cast<int>(status);
        return false;
    }
    result.resolved = true;
    return true;
}

}  // namespace ga::bridge
