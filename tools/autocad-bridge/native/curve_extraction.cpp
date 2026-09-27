#include "curve_extraction.h"
#include "curve_sampling.h"
#include "geometry_math.h"
#include "cad_utils.h"
#include "bridge_config.h"
#include "operation_control.h"
#include "dbpl.h"
#include "dbents.h"
#include "dbmline.h"
#include "gemat3d.h"
#include <cmath>

namespace ga::bridge {

bool extractLightweightPolyline(const AcDbPolyline* polyline,
                                const AcGeMatrix3d& transform,
                                const double tolerance,
                                NativePath& result) {
    const unsigned int vertexCount = polyline->numVerts();
    if (vertexCount < 2) {
        result.errorStatus = static_cast<int>(Acad::eInvalidInput);
        return false;
    }
    const bool authoredClosed = polyline->isClosed();
    const unsigned int segmentCount = authoredClosed ? vertexCount : vertexCount - 1;
    if (segmentCount > kMaximumTopologyElementsPerEntity) {
        result.errorStatus = static_cast<int>(Acad::eOutOfRange);
        result.reason = "native curve exceeded the bounded topology-element budget";
        return false;
    }
    for (unsigned int index = 0; index < segmentCount; ++index) {
        if (operationCancelled()) return false;
        const AcDbPolyline::SegType segmentType = polyline->segType(index);
        bool sampled = false;
        if (segmentType == AcDbPolyline::kLine) {
            AcGeLineSeg3d segment;
            if (polyline->getLineSegAt(index, segment) == Acad::eOk) {
                sampled = appendSampledCurve(
                    segment, transform, tolerance, result.coordinates,
                    result.sampledMaximumDeviation);
            }
        } else if (segmentType == AcDbPolyline::kArc) {
            AcGeCircArc3d segment;
            if (polyline->getArcSegAt(index, segment) == Acad::eOk) {
                sampled = appendSampledCurve(
                    segment, transform, tolerance, result.coordinates,
                    result.sampledMaximumDeviation);
            }
        } else if (segmentType == AcDbPolyline::kCoincident ||
                   segmentType == AcDbPolyline::kPoint ||
                   segmentType == AcDbPolyline::kEmpty) {
            continue;
        }
        if (!sampled) {
            result.errorStatus = static_cast<int>(
                samplingBudgetExhausted(result.coordinates)
                    ? Acad::eOutOfRange
                    : Acad::eInvalidInput);
            if (samplingBudgetExhausted(result.coordinates)) {
                result.reason = "native curve exceeded the bounded sampling budget";
            } else {
                result.reason = "AutoCAD could not sample a lightweight-polyline segment";
            }
            return false;
        }
    }
    // A PDF import commonly contains thousands of coincident-vertex strokes.
    // AutoCAD explicitly classifies every segment as kCoincident/kPoint/kEmpty.
    // Keep them in coverage as context, not as thousands of failed contours.
    // Do not apply this to failed sampling, non-zero loops or unsupported types.
    if (result.coordinates.empty()) {
        result.calculationContext = true;
        result.method = "autodesk-acdbpolyline-degenerate-segments";
        result.reason = "AutoCAD classifies every polyline segment as coincident, point or empty";
        return false;
    }
    if (authoredClosed && vertexCount == 2 &&
        polyline->segType(0) == AcDbPolyline::kLine &&
        polyline->segType(1) == AcDbPolyline::kLine &&
        result.coordinates.size() == 3) {
        // Two straight segments A->B->A have length but no surface. Preserve
        // their exact locus as a line; never invent a filled polygon or drop it.
        result.coordinates.pop_back();
        result.closed = false;
        result.resolved = true;
        result.method = "autodesk-acdbpolyline-retraced-line";
        return true;
    }
    result.closed = authoredClosed ||
        (result.coordinates.size() >= 2 &&
         pointDistance(result.coordinates.front(), result.coordinates.back()) <=
             tolerance);
    if (result.closed) {
        if (result.coordinates.size() < 4 ||
            pointDistance(result.coordinates.front(), result.coordinates.back()) >
                tolerance) {
            result.errorStatus = static_cast<int>(Acad::eInvalidInput);
            result.reason = "closed AcDbPolyline does not form a valid closed path";
            return false;
        }
        result.coordinates.back() = result.coordinates.front();
    } else if (result.coordinates.size() < 2 ||
               pointDistance(result.coordinates.front(), result.coordinates.back()) <=
                   tolerance) {
        result.errorStatus = static_cast<int>(Acad::eInvalidInput);
        result.reason = "open AcDbPolyline has no usable length";
        return false;
    }
    result.resolved = true;
    return true;
}

bool extractSolidBoundary(const AcDbSolid* solid,
                          const AcGeMatrix3d& transform,
                          const double tolerance,
                          NativePath& result) {
    result.method = "autodesk-acdbsolid-wcs-corners";
    // SOLID is a triangle strip, not a cyclic 0,1,2,3 polygon. Its perimeter
    // follows 0,1,3,2. Triangles duplicate corner 2 at corner 3.
    for (const Adesk::UInt16 index : {0, 1, 3, 2}) {
        AcGePoint3d vertex;
        const auto status = solid->getPointAt(index, vertex);
        if (status != Acad::eOk) {
            result.errorStatus = static_cast<int>(status);
            result.reason = "AutoCAD could not read a SOLID corner";
            return false;
        }
        vertex.transformBy(transform);
        const auto point = point3(vertex);
        if (!finitePoint(point)) {
            result.errorStatus = static_cast<int>(Acad::eInvalidInput);
            result.reason = "SOLID has a non-finite transformed corner";
            return false;
        }
        if (result.coordinates.empty() ||
            pointDistance(result.coordinates.back(), point) > tolerance)
            result.coordinates.push_back(point);
    }
    if (result.coordinates.size() > 1 &&
        pointDistance(result.coordinates.front(), result.coordinates.back()) <= tolerance)
        result.coordinates.pop_back();
    if (result.coordinates.size() < 3) {
        result.calculationContext = true;
        result.reason = "SOLID has fewer than three distinct corners at the requested tolerance";
        return false;
    }
    result.coordinates.push_back(result.coordinates.front());
    result.closed = true;
    result.resolved = true;
    return true;
}

bool extractMlineAxis(const AcDbMline* mline,
                      const AcGeMatrix3d& transform,
                      const double tolerance,
                      NativePath& result) {
    result.method = "autodesk-acdbmline-axis-vertices";
    const int vertexCount = mline->numVertices();
    if (vertexCount < 2) {
        result.errorStatus = static_cast<int>(Acad::eInvalidInput);
        result.reason = "AcDbMline has fewer than two axis vertices";
        return false;
    }
    if (static_cast<std::size_t>(vertexCount) >
        kMaximumTopologyElementsPerEntity) {
        result.errorStatus = static_cast<int>(Acad::eOutOfRange);
        result.reason = "AcDbMline exceeded the bounded vertex budget";
        return false;
    }
    for (int index = 0; index < vertexCount; ++index) {
        if (operationCancelled()) return false;
        AcGePoint3d vertex = mline->vertexAt(index);
        vertex.transformBy(transform);
        const Point3 transformed = point3(vertex);
        if (!finitePoint(transformed)) {
            result.errorStatus = static_cast<int>(Acad::eInvalidInput);
            result.reason = "native AcDbMline axis vertex is not finite";
            return false;
        }
        if (result.coordinates.empty() ||
            projectedPointDistance(result.coordinates.back(), transformed) > tolerance) {
            result.coordinates.push_back(transformed);
        }
    }
    result.closed = mline->closedMline();
    if (result.closed) {
        if (result.coordinates.size() < 3) {
            result.errorStatus = static_cast<int>(Acad::eInvalidInput);
            result.reason = "closed AcDbMline axis has fewer than three vertices";
            return false;
        }
        if (projectedPointDistance(result.coordinates.front(),
                                   result.coordinates.back()) > tolerance) {
            result.coordinates.push_back(result.coordinates.front());
        } else {
            result.coordinates.back() = result.coordinates.front();
        }
        if (result.coordinates.size() < 4) {
            result.errorStatus = static_cast<int>(Acad::eInvalidInput);
            result.reason = "closed AcDbMline axis does not form a path";
            return false;
        }
    } else if (result.coordinates.size() < 2) {
        result.calculationContext = true;
        result.reason = "AcDbMline axis has no length in the admitted WCS XY projection";
        return false;
    }
    result.resolved = true;
    return true;
}

bool extractDatabaseCurve(const AcDbCurve* curve,
                          const AcGeMatrix3d& transform,
                          const double tolerance,
                          NativePath& result) {
    if (const AcDbPolyline* polyline = AcDbPolyline::cast(curve)) {
        result.method = "autodesk-acdbpolyline-segments";
        return extractLightweightPolyline(polyline, transform, tolerance, result);
    }
    if (const AcDbLine* line = AcDbLine::cast(curve)) {
        AcGePoint3d transformedStart = line->startPoint();
        AcGePoint3d transformedEnd = line->endPoint();
        transformedStart.transformBy(transform);
        transformedEnd.transformBy(transform);
        const Point3 startPoint = point3(transformedStart);
        const Point3 endPoint = point3(transformedEnd);
        result.method = "autodesk-acdbline-endpoints";
        if (!finitePoint(startPoint) || !finitePoint(endPoint)) {
            result.errorStatus = static_cast<int>(Acad::eInvalidInput);
            result.reason = "native AcDbLine endpoints are not finite";
            return false;
        }
        if (projectedPointDistance(startPoint, endPoint) <= tolerance) {
            result.calculationContext = true;
            result.method = "autodesk-acdbline-degenerate-wcs-xy";
            result.reason =
                "AcDbLine has no length in the admitted WCS XY projection";
            return false;
        }
        result.coordinates = {startPoint, endPoint};
        result.closed = false;
        result.resolved = true;
        return true;
    }
    result.method = "autodesk-acdbcurve-adaptive-sampling";
    double startParameter = 0.0;
    double endParameter = 0.0;
    if (curve->getStartParam(startParameter) != Acad::eOk ||
        curve->getEndParam(endParameter) != Acad::eOk ||
        !std::isfinite(startParameter) || !std::isfinite(endParameter) ||
        startParameter == endParameter) {
        result.errorStatus = static_cast<int>(Acad::eInvalidInput);
        return false;
    }
    AcGePoint3d start;
    AcGePoint3d end;
    if (curve->getPointAtParam(startParameter, start) != Acad::eOk ||
        curve->getPointAtParam(endParameter, end) != Acad::eOk) {
        result.errorStatus = static_cast<int>(Acad::eInvalidInput);
        return false;
    }
    start.transformBy(transform);
    end.transformBy(transform);
    const Point3 startPoint = point3(start);
    const Point3 endPoint = point3(end);
    result.coordinates.push_back(startPoint);
    if (!sampleDatabaseCurveInterval(
            curve, transform, startParameter, endParameter, startPoint,
            endPoint, tolerance, 0, result.coordinates,
            result.sampledMaximumDeviation)) {
        result.errorStatus = static_cast<int>(
            samplingBudgetExhausted(result.coordinates)
                ? Acad::eOutOfRange
                : Acad::eInvalidInput);
        if (samplingBudgetExhausted(result.coordinates)) {
            result.reason = "native curve exceeded the bounded sampling budget";
        }
        return false;
    }
    result.closed = curve->isClosed() ||
        pointDistance(result.coordinates.front(), result.coordinates.back()) <=
            tolerance;
    if (result.closed) {
        if (pointDistance(result.coordinates.front(), result.coordinates.back()) >
            tolerance) {
            result.errorStatus = static_cast<int>(Acad::eInvalidInput);
            return false;
        }
        result.coordinates.back() = result.coordinates.front();
        if (result.coordinates.size() < 4) {
            result.errorStatus = static_cast<int>(Acad::eInvalidInput);
            return false;
        }
    } else if (result.coordinates.size() < 2 ||
               pointDistance(result.coordinates.front(), result.coordinates.back()) <=
                   tolerance) {
        result.errorStatus = static_cast<int>(Acad::eInvalidInput);
        return false;
    }
    result.resolved = true;
    return true;
}

}  // namespace ga::bridge
