#if defined(_DEBUG) && !defined(AC_FULL_DEBUG)
#error _DEBUG should not be defined for a release ObjectARX build
#endif

#include "rxregsvc.h"
#include "aced.h"
#include "adslib.h"
#include "dbapserv.h"
#include "dbsymtb.h"
#include "dbents.h"
#include "dbcurve.h"
#include "dbdim.h"
#include "dbmline.h"
#include "dbpl.h"
#include "dbhatch.h"
#include "dbregion.h"
#include "acdbxref.h"
#include "gemat3d.h"
#include "AcString.h"
#include "brbrep.h"
#include "brbftrav.h"
#include "brface.h"
#include "brfltrav.h"
#include "brloop.h"
#include "brletrav.h"
#include "gecurv3d.h"
#include "geintrvl.h"
#include "core_rxmfcapi.h"

#include <CommonCrypto/CommonDigest.h>

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cmath>
#include <dirent.h>
#include <fstream>
#include <iomanip>
#include <map>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace {

constexpr const ACHAR* kCommandGroup = _T("GREEN_ATLAS");
constexpr const char* kPluginVersion = "0.1.17";
constexpr double kRequestedToleranceMetres = 0.0001;
constexpr int kMaximumSamplingDepth = 24;
bool gSideDatabaseCapture = false;
std::string gSideDatabaseSourcePath;
bool gLastTopologyExportSucceeded = false;
std::string gLastTopologyExportPath;
std::string gLastTopologyExportError;
bool gMcpRequestInProgress = false;

std::string utf8(const ACHAR* value) {
    return value == nullptr ? std::string() : AcString(value).utf8Str();
}

std::string jsonEscape(const std::string& value) {
    std::ostringstream result;
    for (const unsigned char ch : value) {
        switch (ch) {
        case '\"': result << "\\\""; break;
        case '\\': result << "\\\\"; break;
        case '\b': result << "\\b"; break;
        case '\f': result << "\\f"; break;
        case '\n': result << "\\n"; break;
        case '\r': result << "\\r"; break;
        case '\t': result << "\\t"; break;
        default:
            if (ch < 0x20) {
                result << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                       << static_cast<int>(ch) << std::dec;
            } else {
                result << ch;
            }
        }
    }
    return result.str();
}

std::string sha256File(const std::string& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        return {};
    }

    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    char buffer[64 * 1024];
    while (input.good()) {
        input.read(buffer, sizeof(buffer));
        const auto count = input.gcount();
        if (count > 0) {
            CC_SHA256_Update(&context, buffer, static_cast<CC_LONG>(count));
        }
    }

    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(digest, &context);
    std::ostringstream result;
    result << std::hex << std::setfill('0');
    for (const unsigned char byte : digest) {
        result << std::setw(2) << static_cast<int>(byte);
    }
    return result.str();
}

std::size_t fileSize(const std::string& path) {
    std::ifstream input(path, std::ios::binary | std::ios::ate);
    if (!input) return 0;
    const std::streampos end = input.tellg();
    return end > 0 ? static_cast<std::size_t>(end) : 0;
}

std::string objectHandle(AcDbObject* object) {
    AcDbHandle handle;
    object->getAcDbHandle(handle);
    ACHAR buffer[AcDbHandle::kStrSiz] = {};
    return handle.getIntoAsciiBuffer(buffer) ? utf8(buffer) : std::string();
}

std::string entityHandle(AcDbEntity* entity) {
    return objectHandle(entity);
}

struct RegionMeasurement {
    std::string handle;
    std::string layer;
    double area = 0.0;
    double perimeter = 0.0;
    bool areaValid = false;
    bool perimeterValid = false;
};

struct Point3 {
    double x = 0.0;
    double y = 0.0;
    double z = 0.0;
};

struct RegionLoop {
    std::string role;
    std::vector<Point3> coordinates;
    double sampledMaximumDeviation = 0.0;
};

struct RegionTopology {
    RegionMeasurement measurement;
    std::string sourceLayer;
    std::vector<std::string> instanceChain;
    std::vector<RegionLoop> loops;
    bool resolved = false;
    int errorStatus = 0;
};

struct NativePath {
    std::string handle;
    std::string sourceLayer;
    std::string layer;
    std::vector<std::string> instanceChain;
    std::vector<Point3> coordinates;
    bool closed = false;
    bool resolved = false;
    bool calculationContext = false;
    int errorStatus = 0;
    double sampledMaximumDeviation = 0.0;
    std::string method;
    std::string reason;
};

struct NativePoint {
    std::string handle;
    std::string sourceLayer;
    std::string layer;
    std::vector<std::string> instanceChain;
    Point3 coordinates;
};

struct EntityCoverage {
    std::string handle;
    std::string entityType;
    std::string sourceLayer;
    std::string layer;
    std::vector<std::string> instanceChain;
    std::string status;
    std::string method;
    std::string reason;
    std::string xrefDependencyId;
};

struct XrefDependency {
    std::string recordHandle;
    std::string blockName;
    std::string storedPath;
    std::string resolvedPath;
    std::string sha256;
    std::size_t bytes = 0;
    std::string status;
};

bool isNonCalculationContext(const AcDbEntity* entity) {
    if (entity == nullptr) return false;
    if (entity->isKindOf(AcDbDimension::desc())) {
        return true;
    }
    const std::string entityType = utf8(entity->isA()->name());
    static const char* const types[] = {
        "AcDbAttribute",
        "AcDbAttributeDefinition",
        "AcDbDimension",
        "AcDbFcf",
        "AcDbGeoPositionMarker",
        "AcDbImage",
        "AcDbMLeader",
        "AcDbMText",
        "AcDbOle2Frame",
        "AcDbRasterImage",
        "AcDbTable",
        "AcDbText",
        "AcDbUnderlayReference",
        "AcDbViewport",
        "AcDbWipeout",
    };
    return std::find_if(
               std::begin(types), std::end(types),
               [&entityType](const char* value) { return entityType == value; }) !=
        std::end(types);
}

double unitToMetres(const AcDb::UnitsValue units) {
    switch (units) {
    case AcDb::kUnitsInches: return 0.0254;
    case AcDb::kUnitsFeet: return 0.3048;
    case AcDb::kUnitsMiles: return 1609.344;
    case AcDb::kUnitsMillimeters: return 0.001;
    case AcDb::kUnitsCentimeters: return 0.01;
    case AcDb::kUnitsMeters: return 1.0;
    case AcDb::kUnitsKilometers: return 1000.0;
    case AcDb::kUnitsMicroinches: return 0.0000000254;
    case AcDb::kUnitsMils: return 0.0000254;
    case AcDb::kUnitsYards: return 0.9144;
    case AcDb::kUnitsAngstroms: return 0.0000000001;
    case AcDb::kUnitsNanometers: return 0.000000001;
    case AcDb::kUnitsMicrons: return 0.000001;
    case AcDb::kUnitsDecimeters: return 0.1;
    case AcDb::kUnitsDekameters: return 10.0;
    case AcDb::kUnitsHectometers: return 100.0;
    case AcDb::kUnitsGigameters: return 1000000000.0;
    case AcDb::kUnitsAstronomical: return 149597870700.0;
    case AcDb::kUnitsLightYears: return 9460730472580800.0;
    case AcDb::kUnitsParsecs: return 30856775814913672.0;
    case AcDb::kUnitsUSSurveyFeet: return 1200.0 / 3937.0;
    case AcDb::kUnitsUSSurveyInch: return 100.0 / 3937.0;
    case AcDb::kUnitsUSSurveyYard: return 3600.0 / 3937.0;
    case AcDb::kUnitsUSSurveyMile: return 6336000.0 / 3937.0;
    default: return 0.0;
    }
}

Point3 point3(const AcGePoint3d& point) {
    return {point.x, point.y, point.z};
}

double pointDistance(const Point3& left, const Point3& right) {
    const double dx = left.x - right.x;
    const double dy = left.y - right.y;
    const double dz = left.z - right.z;
    return std::sqrt(dx * dx + dy * dy + dz * dz);
}

double projectedPointDistance(const Point3& left, const Point3& right) {
    const double dx = left.x - right.x;
    const double dy = left.y - right.y;
    return std::sqrt(dx * dx + dy * dy);
}

bool finitePoint(const Point3& point) {
    return std::isfinite(point.x) && std::isfinite(point.y) &&
        std::isfinite(point.z);
}

double pointSegmentDistance(const Point3& point, const Point3& start, const Point3& end) {
    const double dx = end.x - start.x;
    const double dy = end.y - start.y;
    const double dz = end.z - start.z;
    const double lengthSquared = dx * dx + dy * dy + dz * dz;
    if (lengthSquared == 0.0) {
        return pointDistance(point, start);
    }
    const double projection =
        ((point.x - start.x) * dx + (point.y - start.y) * dy +
         (point.z - start.z) * dz) / lengthSquared;
    const double clamped = std::max(0.0, std::min(1.0, projection));
    const Point3 closest = {
        start.x + clamped * dx,
        start.y + clamped * dy,
        start.z + clamped * dz,
    };
    return pointDistance(point, closest);
}

bool sampleCurveInterval(const AcGeCurve3d& curve,
                         const AcGeMatrix3d& transform,
                         const double startParameter,
                         const double endParameter,
                         const Point3& startPoint,
                         const Point3& endPoint,
                         const double tolerance,
                         const int depth,
                         std::vector<Point3>& output,
                         double& sampledMaximumDeviation) {
    const double span = endParameter - startParameter;
    const double quarterParameter = startParameter + span * 0.25;
    const double middleParameter = startParameter + span * 0.5;
    const double threeQuarterParameter = startParameter + span * 0.75;
    AcGePoint3d quarterPoint = curve.evalPoint(quarterParameter);
    AcGePoint3d middlePoint = curve.evalPoint(middleParameter);
    AcGePoint3d threeQuarterPoint = curve.evalPoint(threeQuarterParameter);
    quarterPoint.transformBy(transform);
    middlePoint.transformBy(transform);
    threeQuarterPoint.transformBy(transform);
    const Point3 quarter = point3(quarterPoint);
    const Point3 middle = point3(middlePoint);
    const Point3 threeQuarter = point3(threeQuarterPoint);
    const double deviation = std::max({
        pointSegmentDistance(quarter, startPoint, endPoint),
        pointSegmentDistance(middle, startPoint, endPoint),
        pointSegmentDistance(threeQuarter, startPoint, endPoint),
    });

    if (deviation <= tolerance) {
        sampledMaximumDeviation = std::max(sampledMaximumDeviation, deviation);
        output.push_back(endPoint);
        return true;
    }
    if (depth >= kMaximumSamplingDepth) {
        sampledMaximumDeviation = std::max(sampledMaximumDeviation, deviation);
        return false;
    }

    return sampleCurveInterval(curve, transform, startParameter, middleParameter,
                               startPoint, middle, tolerance, depth + 1,
                               output, sampledMaximumDeviation) &&
           sampleCurveInterval(curve, transform, middleParameter, endParameter,
                               middle, endPoint, tolerance, depth + 1,
                               output, sampledMaximumDeviation);
}

bool appendSampledCurve(const AcGeCurve3d& curve,
                        const AcGeMatrix3d& transform,
                        const double tolerance,
                        std::vector<Point3>& output,
                        double& sampledMaximumDeviation) {
    AcGeInterval interval;
    curve.getInterval(interval);
    if (!interval.isBounded()) {
        return false;
    }
    const double startParameter = interval.lowerBound();
    const double endParameter = interval.upperBound();
    if (!std::isfinite(startParameter) || !std::isfinite(endParameter) ||
        startParameter == endParameter) {
        return false;
    }
    AcGePoint3d transformedStart = curve.evalPoint(startParameter);
    AcGePoint3d transformedEnd = curve.evalPoint(endParameter);
    transformedStart.transformBy(transform);
    transformedEnd.transformBy(transform);
    const Point3 startPoint = point3(transformedStart);
    const Point3 endPoint = point3(transformedEnd);
    if (output.empty()) {
        output.push_back(startPoint);
    } else if (pointDistance(output.back(), startPoint) > tolerance) {
        return false;
    }
    return sampleCurveInterval(curve, transform, startParameter, endParameter,
                               startPoint, endPoint, tolerance, 0,
                               output, sampledMaximumDeviation);
}

bool sampleDatabaseCurveInterval(const AcDbCurve* curve,
                                 const AcGeMatrix3d& transform,
                                 const double startParameter,
                                 const double endParameter,
                                 const Point3& startPoint,
                                 const Point3& endPoint,
                                 const double tolerance,
                                 const int depth,
                                 std::vector<Point3>& output,
                                 double& sampledMaximumDeviation) {
    const double span = endParameter - startParameter;
    const double parameters[] = {
        startParameter + span * 0.25,
        startParameter + span * 0.5,
        startParameter + span * 0.75,
    };
    Point3 sampled[3];
    for (int index = 0; index < 3; ++index) {
        AcGePoint3d value;
        if (curve->getPointAtParam(parameters[index], value) != Acad::eOk) {
            return false;
        }
        value.transformBy(transform);
        sampled[index] = point3(value);
    }
    const double deviation = std::max({
        pointSegmentDistance(sampled[0], startPoint, endPoint),
        pointSegmentDistance(sampled[1], startPoint, endPoint),
        pointSegmentDistance(sampled[2], startPoint, endPoint),
    });
    if (deviation <= tolerance) {
        sampledMaximumDeviation = std::max(sampledMaximumDeviation, deviation);
        output.push_back(endPoint);
        return true;
    }
    if (depth >= kMaximumSamplingDepth) {
        sampledMaximumDeviation = std::max(sampledMaximumDeviation, deviation);
        return false;
    }
    return sampleDatabaseCurveInterval(
               curve, transform, startParameter, parameters[1], startPoint,
               sampled[1], tolerance, depth + 1, output,
               sampledMaximumDeviation) &&
           sampleDatabaseCurveInterval(
               curve, transform, parameters[1], endParameter, sampled[1],
               endPoint, tolerance, depth + 1, output,
               sampledMaximumDeviation);
}

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
    for (unsigned int index = 0; index < segmentCount; ++index) {
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
            result.errorStatus = static_cast<int>(Acad::eInvalidInput);
            return false;
        }
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
            return false;
        }
        result.coordinates.back() = result.coordinates.front();
    } else if (result.coordinates.size() < 2 ||
               pointDistance(result.coordinates.front(), result.coordinates.back()) <=
                   tolerance) {
        result.errorStatus = static_cast<int>(Acad::eInvalidInput);
        return false;
    }
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
    for (int index = 0; index < vertexCount; ++index) {
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
        result.errorStatus = static_cast<int>(Acad::eInvalidInput);
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

bool transformedAreaScale(AcDbRegion* region,
                          const AcGeMatrix3d& transform,
                          double& areaScale) {
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
    while (!faceTraverser.done() && status == AcBr::eOk) {
        ++faceCount;
        AcBrFace face;
        status = faceTraverser.getFace(face);
        if (status != AcBr::eOk) break;

        AcBrFaceLoopTraverser loopTraverser;
        status = loopTraverser.setFace(face);
        if (status != AcBr::eOk) break;

        while (!loopTraverser.done() && status == AcBr::eOk) {
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
            extractedLoop.role = loopType == AcBr::kLoopExterior ? "outer" : "hole";
            AcBrLoopEdgeTraverser edgeTraverser;
            status = edgeTraverser.setLoop(loopTraverser);
            if (status != AcBr::eOk) break;

            while (!edgeTraverser.done() && status == AcBr::eOk) {
                AcGeCurve3d* curve = nullptr;
                status = edgeTraverser.getOrientedCurve(curve);
                if (status != AcBr::eOk || curve == nullptr) {
                    delete curve;
                    break;
                }
                const bool sampled = appendSampledCurve(
                    *curve, transform, tolerance, extractedLoop.coordinates,
                    extractedLoop.sampledMaximumDeviation);
                delete curve;
                if (!sampled) {
                    result.errorStatus = static_cast<int>(AcBr::eUnsuitableGeometry);
                    return false;
                }
                status = edgeTraverser.next();
            }
            if (status != AcBr::eOk) break;
            if (extractedLoop.coordinates.size() < 3) {
                result.errorStatus = static_cast<int>(AcBr::eDegenerateTopology);
                return false;
            }
            if (pointDistance(extractedLoop.coordinates.front(),
                              extractedLoop.coordinates.back()) > tolerance) {
                result.errorStatus = static_cast<int>(AcBr::eTopologyMismatch);
                return false;
            }
            extractedLoop.coordinates.back() = extractedLoop.coordinates.front();
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
        status = faceTraverser.next();
    }

    if (status != AcBr::eOk || faceCount != 1 || result.loops.empty()) {
        result.errorStatus = status == AcBr::eOk
            ? static_cast<int>(AcBr::eUnsuitableTopology)
            : static_cast<int>(status);
        return false;
    }
    result.resolved = true;
    return true;
}

struct RegionTraversalDiagnostics {
    std::size_t blockReferences = 0;
    std::size_t traversedBlockReferences = 0;
    std::size_t cyclicBlockReferences = 0;
    std::size_t xrefBlockReferences = 0;
    std::size_t unloadedXrefBlockReferences = 0;
    std::size_t unresolvedXrefBlockReferences = 0;
    std::size_t expandedMInsertCells = 0;
    std::size_t unexpandedMInsertBlocks = 0;
    std::size_t unreadableBlockRecords = 0;
    std::size_t unreadableEntities = 0;
    std::map<std::string, XrefDependency> xrefDependencies;
};

XrefDependency inspectXrefDependency(AcDbBlockTableRecord* record) {
    XrefDependency dependency;
    dependency.recordHandle = objectHandle(record);
    AcString blockName;
    if (record->getName(blockName) == Acad::eOk) {
        dependency.blockName = utf8(blockName.kwszPtr());
    }
    AcString storedPath;
    if (record->pathName(storedPath) == Acad::eOk) {
        dependency.storedPath = utf8(storedPath.kwszPtr());
    }
    AcString resolvedPath;
    if (!dependency.storedPath.empty() &&
        acdbHostApplicationServices()->findFile(
            resolvedPath,
            storedPath.kwszPtr(),
            record->database(),
            AcDbHostApplicationServices::kXRefDrawing) == Acad::eOk &&
        !resolvedPath.isEmpty()) {
        dependency.resolvedPath = utf8(resolvedPath.kwszPtr());
        dependency.bytes = fileSize(dependency.resolvedPath);
        dependency.sha256 = sha256File(dependency.resolvedPath);
    }
    dependency.status =
        record->xrefStatus() == AcDb::kXrfResolved &&
        !dependency.resolvedPath.empty() && dependency.bytes > 0 &&
        !dependency.sha256.empty()
        ? "resolved"
        : "unresolved";
    return dependency;
}

std::string mInsertCellToken(const std::string& handle,
                             const Adesk::UInt16 row,
                             const Adesk::UInt16 column) {
    return "MINSERT:" + handle + ":R" + std::to_string(row) +
        ":C" + std::to_string(column);
}

AcGeVector3d mInsertCellOffset(const AcDbMInsertBlock* block,
                               const Adesk::UInt16 row,
                               const Adesk::UInt16 column) {
    AcGeVector3d offset(
        static_cast<double>(column) * block->columnSpacing(),
        static_cast<double>(row) * block->rowSpacing(),
        0.0);
    offset.transformBy(AcGeMatrix3d::rotation(
        block->rotation(), AcGeVector3d::kZAxis));
    offset.transformBy(AcGeMatrix3d::planeToWorld(block->normal()));
    return offset;
}

void collectAttachedAttributes(AcDbBlockReference* blockReference,
                               const std::vector<std::string>& instanceChain,
                               const std::string& effectiveLayer,
                               std::vector<EntityCoverage>& coverage,
                               RegionTraversalDiagnostics& diagnostics) {
    AcDbObjectIterator* attributeIterator = blockReference->attributeIterator();
    if (attributeIterator == nullptr) return;
    for (attributeIterator->start(); !attributeIterator->done();
         attributeIterator->step()) {
        AcDbEntity* attribute = nullptr;
        if (acdbOpenObject(attribute, attributeIterator->objectId(),
                           AcDb::kForRead) != Acad::eOk ||
            attribute == nullptr) {
            ++diagnostics.unreadableEntities;
            continue;
        }
        EntityCoverage attributeRecord;
        attributeRecord.handle = entityHandle(attribute);
        attributeRecord.entityType = utf8(attribute->isA()->name());
        attributeRecord.sourceLayer = utf8(attribute->layer());
        attributeRecord.layer = attributeRecord.sourceLayer == "0"
            ? effectiveLayer
            : attributeRecord.sourceLayer;
        attributeRecord.instanceChain = instanceChain;
        attributeRecord.status = "context";
        attributeRecord.method = "autodesk-non-calculation-context";
        attributeRecord.reason =
            "attribute instance is preserved as non-calculation annotation context";
        coverage.push_back(std::move(attributeRecord));
        attribute->close();
    }
    delete attributeIterator;
}

bool objectIdIn(const std::vector<AcDbObjectId>& values, const AcDbObjectId& candidate) {
    return std::find(values.begin(), values.end(), candidate) != values.end();
}

void collectRegionInstances(const AcDbObjectId& recordId,
                            const AcGeMatrix3d& accumulatedTransform,
                            const std::vector<std::string>& instanceChain,
                            const std::string& inheritedLayer,
                            const double tolerance,
                            std::vector<AcDbObjectId>& activeRecords,
                            std::vector<RegionTopology>& regions,
                            std::vector<NativePath>& paths,
                            std::vector<NativePoint>& points,
                            std::vector<EntityCoverage>& coverage,
                            RegionTraversalDiagnostics& diagnostics) {
    if (recordId.isNull() || objectIdIn(activeRecords, recordId)) {
        ++diagnostics.cyclicBlockReferences;
        return;
    }
    activeRecords.push_back(recordId);

    AcDbBlockTableRecord* record = nullptr;
    if (acdbOpenObject(record, recordId, AcDb::kForRead) != Acad::eOk || record == nullptr) {
        ++diagnostics.unreadableBlockRecords;
        activeRecords.pop_back();
        return;
    }
    AcDbBlockTableRecordIterator* iterator = nullptr;
    if (record->newIterator(iterator) != Acad::eOk || iterator == nullptr) {
        ++diagnostics.unreadableBlockRecords;
        record->close();
        activeRecords.pop_back();
        return;
    }

    for (iterator->start(); !iterator->done(); iterator->step()) {
        AcDbEntity* entity = nullptr;
        if (iterator->getEntity(entity, AcDb::kForRead) != Acad::eOk || entity == nullptr) {
            ++diagnostics.unreadableEntities;
            continue;
        }

        if (AcDbBlockReference* blockReference = AcDbBlockReference::cast(entity)) {
            ++diagnostics.blockReferences;
            const AcDbObjectId nestedRecordId = blockReference->blockTableRecord();
            const AcGeMatrix3d blockTransform = blockReference->blockTransform();
            const AcGeMatrix3d nestedTransform =
                accumulatedTransform * blockTransform;
            std::vector<std::string> nestedChain(instanceChain);
            nestedChain.push_back(entityHandle(blockReference));
            const std::string sourceLayer = utf8(blockReference->layer());
            const std::string effectiveLayer =
                sourceLayer == "0" ? inheritedLayer : sourceLayer;
            EntityCoverage record;
            record.handle = entityHandle(blockReference);
            record.entityType = utf8(entity->isA()->name());
            record.sourceLayer = sourceLayer;
            record.layer = effectiveLayer;
            record.instanceChain = instanceChain;
            AcDbMInsertBlock* mInsert = AcDbMInsertBlock::cast(blockReference);
            const bool isMInsert = mInsert != nullptr;
            const bool isInvalidMInsert = isMInsert &&
                (mInsert->rows() == 0 || mInsert->columns() == 0 ||
                 !std::isfinite(mInsert->rowSpacing()) ||
                 !std::isfinite(mInsert->columnSpacing()));
            bool isXref = false;
            bool isUnloadedXref = false;
            bool isUnresolvedXref = false;
            std::string xrefDependencyId;
            int xrefStatus = static_cast<int>(AcDb::kXrfNotAnXref);
            AcDbBlockTableRecord* nestedRecord = nullptr;
            if (acdbOpenObject(nestedRecord, nestedRecordId, AcDb::kForRead) == Acad::eOk &&
                nestedRecord != nullptr) {
                isXref = nestedRecord->isFromExternalReference();
                if (isXref) {
                    const AcDb::XrefStatus status = nestedRecord->xrefStatus();
                    xrefStatus = static_cast<int>(status);
                    isUnloadedXref =
                        nestedRecord->isUnloaded() || status == AcDb::kXrfUnloaded;
                    isUnresolvedXref = status != AcDb::kXrfResolved;
                    XrefDependency dependency = inspectXrefDependency(nestedRecord);
                    xrefDependencyId = "xref/" + dependency.recordHandle;
                    if (dependency.status != "resolved") {
                        isUnresolvedXref = true;
                    }
                    diagnostics.xrefDependencies[dependency.recordHandle] =
                        std::move(dependency);
                }
                nestedRecord->close();
            }
            if (isXref) ++diagnostics.xrefBlockReferences;
            if (isUnloadedXref) ++diagnostics.unloadedXrefBlockReferences;
            if (isUnresolvedXref) ++diagnostics.unresolvedXrefBlockReferences;
            if (isInvalidMInsert) ++diagnostics.unexpandedMInsertBlocks;
            record.status = (isInvalidMInsert || isUnresolvedXref) ? "unresolved" : "context";
            record.method = isInvalidMInsert ? "minsert-not-expanded" :
                (isUnresolvedXref ? "xref-not-resolved" :
                (isXref ? "traverse-xref-reference" :
                (isMInsert ? "traverse-minsert-cells" : "traverse-block-reference")));
            record.reason = isInvalidMInsert
                ? "MINSERT has invalid row, column or spacing data"
                : (isUnresolvedXref
                    ? "external reference status is not resolved (status=" +
                        std::to_string(xrefStatus) + ")"
                    : (isXref
                        ? "resolved external reference traversed with a hashed dependency"
                    : (isMInsert
                        ? "container expanded into " +
                            std::to_string(static_cast<std::size_t>(mInsert->rows()) *
                                           static_cast<std::size_t>(mInsert->columns())) +
                            " cell instances for descendant provenance"
                        : "container instance traversed for descendant provenance")));
            record.xrefDependencyId = xrefDependencyId;
            coverage.push_back(std::move(record));

            if (isInvalidMInsert || isUnresolvedXref) {
                collectAttachedAttributes(blockReference, nestedChain, effectiveLayer,
                                          coverage, diagnostics);
                entity->close();
                continue;
            }

            if (objectIdIn(activeRecords, nestedRecordId)) {
                ++diagnostics.cyclicBlockReferences;
                entity->close();
                continue;
            }
            if (isMInsert) {
                for (Adesk::UInt16 row = 0; row < mInsert->rows(); ++row) {
                    for (Adesk::UInt16 column = 0; column < mInsert->columns(); ++column) {
                        std::vector<std::string> cellChain(instanceChain);
                        cellChain.push_back(mInsertCellToken(
                            entityHandle(blockReference), row, column));
                        const AcGeMatrix3d cellTransform = accumulatedTransform *
                            AcGeMatrix3d::translation(
                                mInsertCellOffset(mInsert, row, column)) *
                            blockTransform;
                        collectAttachedAttributes(blockReference, cellChain,
                                                  effectiveLayer, coverage,
                                                  diagnostics);
                        ++diagnostics.expandedMInsertCells;
                        ++diagnostics.traversedBlockReferences;
                        collectRegionInstances(nestedRecordId, cellTransform, cellChain,
                                               effectiveLayer, tolerance, activeRecords,
                                               regions, paths, points, coverage,
                                               diagnostics);
                    }
                }
            } else {
                collectAttachedAttributes(blockReference, nestedChain, effectiveLayer,
                                          coverage, diagnostics);
                ++diagnostics.traversedBlockReferences;
                collectRegionInstances(nestedRecordId, nestedTransform, nestedChain,
                                       effectiveLayer, tolerance, activeRecords,
                                       regions, paths, points, coverage,
                                       diagnostics);
            }
            entity->close();
            continue;
        }

        if (AcDbRegion* region = AcDbRegion::cast(entity)) {
            RegionTopology topology;
            topology.measurement.handle = entityHandle(region);
            topology.sourceLayer = utf8(region->layer());
            topology.measurement.layer = topology.sourceLayer == "0"
                ? inheritedLayer
                : topology.sourceLayer;
            topology.instanceChain = instanceChain;
            extractRegionTopology(region, accumulatedTransform, tolerance, topology);
            EntityCoverage record;
            record.handle = topology.measurement.handle;
            record.entityType = utf8(entity->isA()->name());
            record.sourceLayer = topology.sourceLayer;
            record.layer = topology.measurement.layer;
            record.instanceChain = instanceChain;
            record.status = topology.resolved ? "native" : "unresolved";
            record.method = topology.resolved
                ? "autodesk-acbr-local-affine"
                : "autodesk-acbr-local-affine-failed";
            if (!topology.resolved) {
                record.reason = "native REGION topology extraction failed";
            }
            coverage.push_back(std::move(record));
            regions.push_back(std::move(topology));
        } else if (AcDbHatch* hatch = AcDbHatch::cast(entity)) {
            RegionTopology topology;
            topology.measurement.handle = entityHandle(hatch);
            topology.sourceLayer = utf8(hatch->layer());
            topology.measurement.layer = topology.sourceLayer == "0"
                ? inheritedLayer
                : topology.sourceLayer;
            topology.instanceChain = instanceChain;
            AcDbRegion* region = hatch->getRegionArea();
            const bool regionAvailable = region != nullptr;
            if (region != nullptr) {
                extractRegionTopology(
                    region, accumulatedTransform, tolerance, topology);
                delete region;
            }
            EntityCoverage record;
            record.handle = topology.measurement.handle;
            record.entityType = utf8(entity->isA()->name());
            record.sourceLayer = topology.sourceLayer;
            record.layer = topology.measurement.layer;
            record.instanceChain = instanceChain;
            record.status = topology.resolved ? "native" : "unresolved";
            record.method = topology.resolved
                ? "autodesk-hatch-region-acbr-local-affine"
                : "autodesk-hatch-region-acbr-local-affine-failed";
            if (!topology.resolved) {
                double hatchArea = 0.0;
                const Acad::ErrorStatus areaStatus = hatch->getArea(hatchArea);
                std::size_t openLoops = 0;
                std::size_t selfIntersectingLoops = 0;
                std::size_t polylineLoops = 0;
                std::size_t textLoops = 0;
                const int loopCount = hatch->numLoops();
                for (int loopIndex = 0; loopIndex < loopCount; ++loopIndex) {
                    const Adesk::Int32 loopType = hatch->loopTypeAt(loopIndex);
                    if ((loopType & AcDbHatch::kNotClosed) != 0) ++openLoops;
                    if ((loopType & AcDbHatch::kSelfIntersecting) != 0) {
                        ++selfIntersectingLoops;
                    }
                    if ((loopType & AcDbHatch::kPolyline) != 0) ++polylineLoops;
                    if ((loopType & (AcDbHatch::kTextbox |
                                     AcDbHatch::kTextIsland)) != 0) {
                        ++textLoops;
                    }
                }
                record.reason =
                    "native HATCH topology extraction failed: region=" +
                    std::string(regionAvailable ? "available" : "null") +
                    ", topology_status=" + std::to_string(topology.errorStatus) +
                    ", area_status=" + std::to_string(static_cast<int>(areaStatus)) +
                    ", area=" +
                    (areaStatus == Acad::eOk && std::isfinite(hatchArea)
                        ? std::to_string(hatchArea)
                        : std::string("unavailable")) +
                    ", loops=" + std::to_string(loopCount) +
                    ", polyline_loops=" + std::to_string(polylineLoops) +
                    ", open_loops=" + std::to_string(openLoops) +
                    ", self_intersecting_loops=" +
                    std::to_string(selfIntersectingLoops) +
                    ", text_loops=" + std::to_string(textLoops);
            }
            coverage.push_back(std::move(record));
            regions.push_back(std::move(topology));
        } else if (AcDbMline* mline = AcDbMline::cast(entity)) {
            NativePath path;
            path.handle = entityHandle(mline);
            path.sourceLayer = utf8(mline->layer());
            path.layer = path.sourceLayer == "0" ? inheritedLayer : path.sourceLayer;
            path.instanceChain = instanceChain;
            extractMlineAxis(mline, accumulatedTransform, tolerance, path);
            EntityCoverage record;
            record.handle = path.handle;
            record.entityType = utf8(entity->isA()->name());
            record.sourceLayer = path.sourceLayer;
            record.layer = path.layer;
            record.instanceChain = instanceChain;
            record.status = path.resolved
                ? "native"
                : (path.calculationContext ? "context" : "unresolved");
            record.method = path.method +
                (path.resolved || path.calculationContext ? "" : "-failed");
            if (!path.resolved) {
                record.reason = path.reason.empty()
                    ? "native AcDbMline axis extraction failed"
                    : path.reason;
            }
            coverage.push_back(std::move(record));
            if (path.resolved) paths.push_back(std::move(path));
        } else if (AcDbCurve* curve = AcDbCurve::cast(entity)) {
            NativePath path;
            path.handle = entityHandle(curve);
            path.sourceLayer = utf8(curve->layer());
            path.layer = path.sourceLayer == "0" ? inheritedLayer : path.sourceLayer;
            path.instanceChain = instanceChain;
            extractDatabaseCurve(curve, accumulatedTransform, tolerance, path);
            EntityCoverage record;
            record.handle = path.handle;
            record.entityType = utf8(entity->isA()->name());
            record.sourceLayer = path.sourceLayer;
            record.layer = path.layer;
            record.instanceChain = instanceChain;
            record.status = path.resolved
                ? "native"
                : (path.calculationContext ? "context" : "unresolved");
            record.method = path.method +
                (path.resolved || path.calculationContext ? "" : "-failed");
            if (!path.resolved) {
                record.reason = path.reason.empty()
                    ? "native finite curve extraction failed"
                    : path.reason;
            }
            coverage.push_back(std::move(record));
            if (path.resolved) paths.push_back(std::move(path));
        } else if (AcDbPoint* point = AcDbPoint::cast(entity)) {
            NativePoint nativePoint;
            nativePoint.handle = entityHandle(point);
            nativePoint.sourceLayer = utf8(point->layer());
            nativePoint.layer = nativePoint.sourceLayer == "0"
                ? inheritedLayer
                : nativePoint.sourceLayer;
            nativePoint.instanceChain = instanceChain;
            AcGePoint3d position = point->position();
            position.transformBy(accumulatedTransform);
            nativePoint.coordinates = point3(position);
            EntityCoverage record;
            record.handle = nativePoint.handle;
            record.entityType = utf8(entity->isA()->name());
            record.sourceLayer = nativePoint.sourceLayer;
            record.layer = nativePoint.layer;
            record.instanceChain = instanceChain;
            record.status = "native";
            record.method = "autodesk-acdbpoint-wcs";
            coverage.push_back(std::move(record));
            points.push_back(std::move(nativePoint));
        } else {
            EntityCoverage record;
            record.handle = entityHandle(entity);
            record.entityType = utf8(entity->isA()->name());
            record.sourceLayer = utf8(entity->layer());
            record.layer = record.sourceLayer == "0" ? inheritedLayer : record.sourceLayer;
            record.instanceChain = instanceChain;
            if (isNonCalculationContext(entity)) {
                record.status = "context";
                record.method = "autodesk-non-calculation-context";
                record.reason =
                    "known annotation or presentation entity is not calculation geometry";
            } else {
                record.status = "unresolved";
                record.method = "autodesk-native-geometry-not-implemented";
                record.reason =
                    "entity instance is inventoried but native geometry is not implemented";
            }
            coverage.push_back(std::move(record));
        }
        entity->close();
    }

    delete iterator;
    record->close();
    activeRecords.pop_back();
}

void exportProbe() {
    AcDbDatabase* database = acdbHostApplicationServices()->workingDatabase();
    if (database == nullptr) {
        acutPrintf(_T("\nGreen Atlas: no active drawing."));
        return;
    }

    resbuf databaseModification = {};
    const bool databaseModificationKnown =
        acedGetVar(_T("DBMOD"), &databaseModification) == RTNORM &&
        databaseModification.restype == RTSHORT;
    const int databaseModificationFlags =
        databaseModificationKnown ? databaseModification.resval.rint : -1;
    if (!databaseModificationKnown || databaseModificationFlags != 0) {
        const AcString databaseModificationText(
            databaseModificationKnown ? std::to_string(databaseModificationFlags).c_str() : "unknown");
        acutPrintf(
            _T("\nGreen Atlas: the live drawing has unsaved changes (DBMOD=%s); "
               "reopen or save a deliberate copy before exporting."),
            databaseModificationText.kwszPtr());
        return;
    }

    const ACHAR* sourceName = nullptr;
    if (database->getFilename(sourceName) != Acad::eOk || sourceName == nullptr || *sourceName == 0) {
        acutPrintf(_T("\nGreen Atlas: save the drawing before exporting a probe."));
        return;
    }

    const std::string sourcePath = utf8(sourceName);
    const std::string sourceHash = sha256File(sourcePath);
    if (sourceHash.empty()) {
        acutPrintf(_T("\nGreen Atlas: cannot read the saved source file."));
        return;
    }

    AcDbBlockTable* blockTable = nullptr;
    if (database->getBlockTable(blockTable, AcDb::kForRead) != Acad::eOk) {
        acutPrintf(_T("\nGreen Atlas: cannot open the block table."));
        return;
    }

    AcDbBlockTableRecord* modelSpace = nullptr;
    const Acad::ErrorStatus modelStatus =
        blockTable->getAt(ACDB_MODEL_SPACE, modelSpace, AcDb::kForRead);
    blockTable->close();
    if (modelStatus != Acad::eOk || modelSpace == nullptr) {
        acutPrintf(_T("\nGreen Atlas: cannot open model space."));
        return;
    }

    AcDbBlockTableRecordIterator* iterator = nullptr;
    if (modelSpace->newIterator(iterator) != Acad::eOk || iterator == nullptr) {
        modelSpace->close();
        acutPrintf(_T("\nGreen Atlas: cannot enumerate model space."));
        return;
    }

    std::map<std::string, std::size_t> classes;
    std::vector<RegionMeasurement> regions;
    std::size_t entityCount = 0;

    for (iterator->start(); !iterator->done(); iterator->step()) {
        AcDbEntity* entity = nullptr;
        if (iterator->getEntity(entity, AcDb::kForRead) != Acad::eOk || entity == nullptr) {
            continue;
        }

        ++entityCount;
        ++classes[utf8(entity->isA()->name())];

        if (AcDbRegion* region = AcDbRegion::cast(entity)) {
            RegionMeasurement measurement;
            measurement.handle = entityHandle(region);
            measurement.layer = utf8(region->layer());
            measurement.areaValid = region->getArea(measurement.area) == Acad::eOk;
            measurement.perimeterValid = region->getPerimeter(measurement.perimeter) == Acad::eOk;
            regions.push_back(std::move(measurement));
        }

        entity->close();
    }

    delete iterator;
    modelSpace->close();

    AcString revision;
    database->getVersionGuid(revision);
    const std::string outputPath = sourcePath + ".green-atlas.probe.json";
    const std::string temporaryPath = outputPath + ".tmp";

    std::ofstream output(temporaryPath, std::ios::binary | std::ios::trunc);
    if (!output) {
        acutPrintf(_T("\nGreen Atlas: cannot create the sidecar file."));
        return;
    }

    output << std::setprecision(17);
    output << "{\n"
           << "  \"schema\": \"green-atlas.autocad-probe/1\",\n"
           << "  \"complete\": false,\n"
           << "  \"plugin_version\": \"" << kPluginVersion << "\",\n"
           << "  \"source\": {\n"
           << "    \"path\": \"" << jsonEscape(sourcePath) << "\",\n"
           << "    \"sha256\": \"" << sourceHash << "\",\n"
           << "    \"units_code\": " << static_cast<int>(database->insunits()) << ",\n"
           << "    \"document_revision\": \"" << jsonEscape(revision.utf8Str()) << "\",\n"
           << "    \"database_modified_flags\": ";
    if (databaseModificationKnown) output << databaseModificationFlags; else output << "null";
    output << ",\n"
           << "    \"live_database_matches_disk\": ";
    if (!databaseModificationKnown) output << "null";
    else output << (databaseModificationFlags == 0 ? "true" : "null");
    output << "\n"
           << "  },\n"
           << "  \"model_space_entities\": " << entityCount << ",\n"
           << "  \"classes\": {";

    bool first = true;
    for (const auto& [name, count] : classes) {
        output << (first ? "\n" : ",\n")
               << "    \"" << jsonEscape(name) << "\": " << count;
        first = false;
    }
    if (!classes.empty()) {
        output << '\n';
    }
    output << "  },\n  \"regions\": [";

    for (std::size_t index = 0; index < regions.size(); ++index) {
        const RegionMeasurement& region = regions[index];
        output << (index == 0 ? "\n" : ",\n")
               << "    {\"handle\": \"" << jsonEscape(region.handle)
               << "\", \"layer\": \"" << jsonEscape(region.layer) << "\", "
               << "\"area\": ";
        if (region.areaValid) output << region.area; else output << "null";
        output << ", \"perimeter\": ";
        if (region.perimeterValid) output << region.perimeter; else output << "null";
        output << '}';
    }
    if (!regions.empty()) {
        output << '\n';
    }
    output << "  ],\n"
           << "  \"limitations\": [\"model-space roots only\", "
              "\"nested INSERT/XREF instances not expanded\", "
              "\"REGION topology not tessellated yet\", "
              "\"nonzero DBMOD can be intrinsic to opening DXF and does not identify user edits\"]\n"
           << "}\n";
    output.close();

    if (!output || std::rename(temporaryPath.c_str(), outputPath.c_str()) != 0) {
        std::remove(temporaryPath.c_str());
        acutPrintf(_T("\nGreen Atlas: failed to publish the sidecar atomically."));
        return;
    }

    const AcString entityCountText(std::to_string(entityCount).c_str());
    const AcString regionCountText(std::to_string(regions.size()).c_str());
    const AcString databaseModificationText(
        databaseModificationKnown ? std::to_string(databaseModificationFlags).c_str() : "unknown");
    acutPrintf(_T("\nGreen Atlas: probe exported (%s entities, %s regions, DBMOD=%s)."),
               entityCountText.kwszPtr(), regionCountText.kwszPtr(),
               databaseModificationText.kwszPtr());
    acutPrintf(_T("\n%s"), AcString(outputPath.c_str()).kwszPtr());
}

void exportRegionTopologyProbe() {
    gLastTopologyExportSucceeded = false;
    gLastTopologyExportPath.clear();
    gLastTopologyExportError = "native topology export did not complete";
    AcDbDatabase* database = acdbHostApplicationServices()->workingDatabase();
    if (database == nullptr) {
        gLastTopologyExportError = "no active AutoCAD database";
        acutPrintf(_T("\nGreen Atlas: no active drawing."));
        return;
    }

    resbuf databaseModification = {};
    const bool databaseModificationKnown = gSideDatabaseCapture ||
        (acedGetVar(_T("DBMOD"), &databaseModification) == RTNORM &&
         databaseModification.restype == RTSHORT);
    const int databaseModificationFlags = gSideDatabaseCapture
        ? 0
        : (databaseModificationKnown ? databaseModification.resval.rint : -1);
    if (!databaseModificationKnown) {
        gLastTopologyExportError = "DBMOD is unavailable";
        acutPrintf(_T("\nGreen Atlas: DBMOD is unavailable; topology provenance cannot be recorded."));
        return;
    }

    const ACHAR* sourceName = nullptr;
    if (!gSideDatabaseCapture &&
        (database->getFilename(sourceName) != Acad::eOk ||
         sourceName == nullptr || *sourceName == 0)) {
        gLastTopologyExportError = "drawing must be saved before export";
        acutPrintf(_T("\nGreen Atlas: save the drawing before exporting topology."));
        return;
    }
    const std::string sourcePath = gSideDatabaseCapture
        ? gSideDatabaseSourcePath
        : utf8(sourceName);
    const std::string sourceHash = sha256File(sourcePath);
    if (sourceHash.empty()) {
        gLastTopologyExportError = "saved source file cannot be read";
        acutPrintf(_T("\nGreen Atlas: cannot read the saved source file."));
        return;
    }

    const double metresPerUnit = unitToMetres(database->insunits());
    if (metresPerUnit <= 0.0) {
        gLastTopologyExportError = "drawing units are undefined";
        acutPrintf(_T("\nGreen Atlas: drawing units are undefined; topology tolerance cannot be proven."));
        return;
    }
    const double toleranceUnits = kRequestedToleranceMetres / metresPerUnit;

    AcDbBlockTable* blockTable = nullptr;
    if (database->getBlockTable(blockTable, AcDb::kForRead) != Acad::eOk) {
        gLastTopologyExportError = "block table cannot be opened";
        acutPrintf(_T("\nGreen Atlas: cannot open the block table."));
        return;
    }
    AcDbObjectId modelSpaceId;
    const Acad::ErrorStatus modelStatus =
        blockTable->getAt(ACDB_MODEL_SPACE, modelSpaceId);
    blockTable->close();
    if (modelStatus != Acad::eOk || modelSpaceId.isNull()) {
        gLastTopologyExportError = "model space cannot be opened";
        acutPrintf(_T("\nGreen Atlas: cannot open model space."));
        return;
    }

    std::vector<RegionTopology> regions;
    std::vector<NativePath> paths;
    std::vector<NativePoint> points;
    std::vector<EntityCoverage> coverage;
    RegionTraversalDiagnostics traversal;
    const AcGeMatrix3d rootTransform = AcGeMatrix3d::kIdentity;
    std::vector<std::string> instanceChain;
    std::vector<AcDbObjectId> activeRecords;
    collectRegionInstances(modelSpaceId, rootTransform, instanceChain, "0",
                           toleranceUnits, activeRecords, regions, paths, points,
                           coverage, traversal);

    std::size_t resolvedCount = 0;
    std::size_t loopCount = 0;
    std::size_t pointCount = 0;
    for (const RegionTopology& region : regions) {
        if (region.resolved) ++resolvedCount;
        loopCount += region.loops.size();
        for (const RegionLoop& loop : region.loops) pointCount += loop.coordinates.size();
    }
    std::map<std::string, std::size_t> coverageStatusCounts;
    for (const EntityCoverage& record : coverage) {
        ++coverageStatusCounts[record.status];
    }

    AcString revision;
    database->getVersionGuid(revision);
    const std::string outputPath = sourcePath + ".green-atlas.geometry.json";
    const std::string temporaryPath = outputPath + ".tmp";
    std::ofstream output(temporaryPath, std::ios::binary | std::ios::trunc);
    if (!output) {
        gLastTopologyExportError = "topology sidecar cannot be created";
        acutPrintf(_T("\nGreen Atlas: cannot create the topology sidecar file."));
        return;
    }

    output << std::setprecision(17);
    output << "{\n"
           << "  \"schema\": \"green-atlas.autocad-region-topology-probe/1\",\n"
           << "  \"complete\": false,\n"
           << "  \"plugin_version\": \"" << kPluginVersion << "\",\n"
           << "  \"capture_mode\": \""
           << (gSideDatabaseCapture ? "side_database_dxf" : "live_document")
           << "\",\n"
           << "  \"requested_tolerance_m\": " << kRequestedToleranceMetres << ",\n"
           << "  \"source\": {\n"
           << "    \"path\": \"" << jsonEscape(sourcePath) << "\",\n"
           << "    \"sha256\": \"" << sourceHash << "\",\n"
           << "    \"units_code\": " << static_cast<int>(database->insunits()) << ",\n"
           << "    \"metres_per_unit\": " << metresPerUnit << ",\n"
           << "    \"document_revision\": \"" << jsonEscape(revision.utf8Str()) << "\",\n"
           << "    \"database_modified_flags\": " << databaseModificationFlags << ",\n"
           << "    \"live_database_matches_disk\": "
           << (databaseModificationFlags == 0 ? "true" : "false") << "\n"
           << "  },\n"
           << "  \"xref_dependencies\": [";
    std::size_t dependencyIndex = 0;
    for (const auto& [recordHandle, dependency] : traversal.xrefDependencies) {
        output << (dependencyIndex++ == 0 ? "\n" : ",\n")
               << "    {\"record_handle\": \"" << jsonEscape(recordHandle)
               << "\", \"block_name\": \"" << jsonEscape(dependency.blockName)
               << "\", \"stored_path\": \"" << jsonEscape(dependency.storedPath)
               << "\", \"resolved_path\": \"" << jsonEscape(dependency.resolvedPath)
               << "\", \"sha256\": \"" << jsonEscape(dependency.sha256)
               << "\", \"bytes\": " << dependency.bytes
               << ", \"status\": \"" << dependency.status << "\"}";
    }
    if (!traversal.xrefDependencies.empty()) output << '\n';
    output << "  ],\n"
           << "  \"coverage\": [";

    for (std::size_t coverageIndex = 0;
         coverageIndex < coverage.size(); ++coverageIndex) {
        const EntityCoverage& record = coverage[coverageIndex];
        output << (coverageIndex == 0 ? "\n" : ",\n")
               << "    {\"handle\": \"" << jsonEscape(record.handle)
               << "\", \"entity_type\": \"" << jsonEscape(record.entityType)
               << "\", \"source_layer\": \"" << jsonEscape(record.sourceLayer)
               << "\", \"layer\": \"" << jsonEscape(record.layer)
               << "\", \"instance_chain\": [";
        for (std::size_t chainIndex = 0;
             chainIndex < record.instanceChain.size(); ++chainIndex) {
            output << (chainIndex == 0 ? "" : ",")
                   << "\"" << jsonEscape(record.instanceChain[chainIndex]) << "\"";
        }
        output << "], \"status\": \"" << record.status
               << "\", \"method\": \"" << record.method
               << "\", \"reason\": ";
        if (record.reason.empty()) output << "null";
        else output << "\"" << jsonEscape(record.reason) << "\"";
        output << ", \"xref_dependency_id\": ";
        if (record.xrefDependencyId.empty()) output << "null";
        else output << "\"" << jsonEscape(record.xrefDependencyId) << "\"";
        output << '}';
    }
    if (!coverage.empty()) output << '\n';
    output << "  ],\n"
           << "  \"regions\": [";

    for (std::size_t regionIndex = 0; regionIndex < regions.size(); ++regionIndex) {
        const RegionTopology& region = regions[regionIndex];
        output << (regionIndex == 0 ? "\n" : ",\n")
               << "    {\"handle\": \"" << jsonEscape(region.measurement.handle)
               << "\", \"source_layer\": \"" << jsonEscape(region.sourceLayer)
               << "\", \"layer\": \"" << jsonEscape(region.measurement.layer)
               << "\", \"instance_chain\": [";
        for (std::size_t chainIndex = 0;
             chainIndex < region.instanceChain.size(); ++chainIndex) {
            output << (chainIndex == 0 ? "" : ",")
                   << "\"" << jsonEscape(region.instanceChain[chainIndex]) << "\"";
        }
        output << "], \"status\": \"" << (region.resolved ? "native" : "unresolved")
               << "\", \"error_status\": ";
        if (region.resolved) output << "null"; else output << region.errorStatus;
        output << ", \"native_area_units2\": ";
        if (region.measurement.areaValid) output << region.measurement.area; else output << "null";
        output << ", \"native_perimeter_units\": ";
        if (region.measurement.perimeterValid) output << region.measurement.perimeter; else output << "null";
        output << ", \"loops\": [";

        for (std::size_t loopIndex = 0; loopIndex < region.loops.size(); ++loopIndex) {
            const RegionLoop& loop = region.loops[loopIndex];
            output << (loopIndex == 0 ? "" : ",")
                   << "{\"role\": \"" << loop.role
                   << "\", \"sampled_max_deviation_units\": "
                   << loop.sampledMaximumDeviation
                   << ", \"coordinates\": [";
            for (std::size_t pointIndex = 0; pointIndex < loop.coordinates.size(); ++pointIndex) {
                const Point3& point = loop.coordinates[pointIndex];
                output << (pointIndex == 0 ? "" : ",")
                       << '[' << point.x << ',' << point.y << ',' << point.z << ']';
            }
            output << "]}";
        }
        output << "]}";
    }
    if (!regions.empty()) output << '\n';
    output << "  ],\n"
           << "  \"paths\": [";
    for (std::size_t pathIndex = 0; pathIndex < paths.size(); ++pathIndex) {
        const NativePath& path = paths[pathIndex];
        output << (pathIndex == 0 ? "\n" : ",\n")
               << "    {\"handle\": \"" << jsonEscape(path.handle)
               << "\", \"source_layer\": \"" << jsonEscape(path.sourceLayer)
               << "\", \"layer\": \"" << jsonEscape(path.layer)
               << "\", \"instance_chain\": [";
        for (std::size_t chainIndex = 0;
             chainIndex < path.instanceChain.size(); ++chainIndex) {
            output << (chainIndex == 0 ? "" : ",")
                   << "\"" << jsonEscape(path.instanceChain[chainIndex]) << "\"";
        }
        output << "], \"status\": \"native\", \"error_status\": null"
               << ", \"closed\": " << (path.closed ? "true" : "false")
               << ", \"sampled_max_deviation_units\": "
               << path.sampledMaximumDeviation
               << ", \"coordinates\": [";
        for (std::size_t pointIndex = 0;
             pointIndex < path.coordinates.size(); ++pointIndex) {
            const Point3& point = path.coordinates[pointIndex];
            output << (pointIndex == 0 ? "" : ",")
                   << '[' << point.x << ',' << point.y << ',' << point.z << ']';
        }
        output << "]}";
    }
    if (!paths.empty()) output << '\n';
    output << "  ],\n"
           << "  \"points\": [";
    for (std::size_t pointIndex = 0; pointIndex < points.size(); ++pointIndex) {
        const NativePoint& point = points[pointIndex];
        output << (pointIndex == 0 ? "\n" : ",\n")
               << "    {\"handle\": \"" << jsonEscape(point.handle)
               << "\", \"source_layer\": \"" << jsonEscape(point.sourceLayer)
               << "\", \"layer\": \"" << jsonEscape(point.layer)
               << "\", \"instance_chain\": [";
        for (std::size_t chainIndex = 0;
             chainIndex < point.instanceChain.size(); ++chainIndex) {
            output << (chainIndex == 0 ? "" : ",")
                   << "\"" << jsonEscape(point.instanceChain[chainIndex]) << "\"";
        }
        output << "], \"status\": \"native\", \"error_status\": null"
               << ", \"coordinates\": ["
               << point.coordinates.x << ',' << point.coordinates.y << ','
               << point.coordinates.z << "]}";
    }
    if (!points.empty()) output << '\n';
    output << "  ],\n"
           << "  \"summary\": {\"regions\": " << regions.size()
           << ", \"paths\": " << paths.size()
           << ", \"points\": " << points.size()
           << ", \"resolved\": " << resolvedCount
           << ", \"unresolved\": " << (regions.size() - resolvedCount)
           << ", \"loops\": " << loopCount
           << ", \"region_sampled_points\": " << pointCount
           << ", \"block_references\": " << traversal.blockReferences
           << ", \"traversed_block_references\": " << traversal.traversedBlockReferences
           << ", \"cyclic_block_references\": " << traversal.cyclicBlockReferences
           << ", \"xref_block_references\": " << traversal.xrefBlockReferences
           << ", \"unloaded_xref_block_references\": "
           << traversal.unloadedXrefBlockReferences
           << ", \"unresolved_xref_block_references\": "
           << traversal.unresolvedXrefBlockReferences
           << ", \"xref_dependency_records\": "
           << traversal.xrefDependencies.size()
           << ", \"expanded_minsert_cells\": "
           << traversal.expandedMInsertCells
           << ", \"unexpanded_minsert_blocks\": "
           << traversal.unexpandedMInsertBlocks
           << ", \"unreadable_block_records\": " << traversal.unreadableBlockRecords
           << ", \"unreadable_entities\": " << traversal.unreadableEntities
           << ", \"source_instances\": " << coverage.size()
           << ", \"native\": " << coverageStatusCounts["native"]
           << ", \"context\": " << coverageStatusCounts["context"]
           << ", \"unresolved_instances\": " << coverageStatusCounts["unresolved"]
           << "},\n"
           << "  \"limitations\": ["
              "\"REGION, HATCH area, finite curves and points are emitted; other non-context entities remain explicit unresolved coverage\", "
              "\"resolved XREF files are hashed and unresolved references fail admission\", "
              "\"nonzero DBMOD makes the live snapshot diagnostic-only\", "
              "\"adaptive chord checks require independent admission verification\"]\n"
           << "}\n";
    output.close();

    if (!output || std::rename(temporaryPath.c_str(), outputPath.c_str()) != 0) {
        std::remove(temporaryPath.c_str());
        gLastTopologyExportError = "topology sidecar could not be published atomically";
        acutPrintf(_T("\nGreen Atlas: failed to publish the topology sidecar atomically."));
        return;
    }

    gLastTopologyExportSucceeded = true;
    gLastTopologyExportPath = outputPath;
    gLastTopologyExportError.clear();

    const AcString regionCountText(std::to_string(regions.size()).c_str());
    const AcString resolvedCountText(std::to_string(resolvedCount).c_str());
    acutPrintf(_T("\nGreen Atlas: REGION topology probe exported (%s/%s resolved)."),
               resolvedCountText.kwszPtr(), regionCountText.kwszPtr());
    if (!gSideDatabaseCapture && databaseModificationFlags != 0) {
        const AcString databaseModificationText(
            std::to_string(databaseModificationFlags).c_str());
        acutPrintf(_T("\nGreen Atlas: diagnostic-only snapshot (DBMOD=%s)."),
                   databaseModificationText.kwszPtr());
    }
    acutPrintf(_T("\n%s"), AcString(outputPath.c_str()).kwszPtr());
}

bool exportRegionTopologyFromPath(const std::string& sourcePath) {
    gLastTopologyExportSucceeded = false;
    gLastTopologyExportPath.clear();
    gLastTopologyExportError.clear();
    if (sourcePath.size() < 4 ||
        sourcePath.substr(sourcePath.size() - 4) != ".dxf") {
        gLastTopologyExportError = "only saved DXF sources are accepted";
        acutPrintf(_T("\nGreen Atlas: file-based topology export currently accepts DXF only."));
        return false;
    }

    AcDbDatabase sourceDatabase(false, true);
    const std::string logPath = sourcePath + ".green-atlas.dxf.log";
    const Acad::ErrorStatus importStatus = sourceDatabase.dxfIn(
        AcString(sourcePath.c_str()).kwszPtr(), AcString(logPath.c_str()).kwszPtr());
    if (importStatus != Acad::eOk) {
        gLastTopologyExportError =
            "isolated AutoCAD DXF import failed with status " +
            std::to_string(static_cast<int>(importStatus));
        const AcString statusText(std::to_string(static_cast<int>(importStatus)).c_str());
        acutPrintf(_T("\nGreen Atlas: isolated DXF import failed (status=%s)."),
                   statusText.kwszPtr());
        return false;
    }

    // Resolve package-local XREF databases in memory. The root DXF and every
    // referenced file remain read-only; the emitted ledger binds native
    // descendants to the exact dependency bytes used by AutoCAD.
    acdbResolveCurrentXRefs(&sourceDatabase, false, false);

    AcDbHostApplicationServices* services = acdbHostApplicationServices();
    AcDbDatabase* previousDatabase = services->workingDatabase();
    services->setWorkingDatabase(&sourceDatabase);
    gSideDatabaseCapture = true;
    gSideDatabaseSourcePath = sourcePath;
    exportRegionTopologyProbe();
    gSideDatabaseSourcePath.clear();
    gSideDatabaseCapture = false;
    services->setWorkingDatabase(previousDatabase);
    return gLastTopologyExportSucceeded;
}

void exportRegionTopologyFromSourceFile() {
    AcDbDatabase* liveDatabase = acdbHostApplicationServices()->workingDatabase();
    if (liveDatabase == nullptr) {
        acutPrintf(_T("\nGreen Atlas: no active drawing."));
        return;
    }
    const ACHAR* sourceName = nullptr;
    if (liveDatabase->getFilename(sourceName) != Acad::eOk ||
        sourceName == nullptr || *sourceName == 0) {
        acutPrintf(_T("\nGreen Atlas: open a saved DXF before file-based export."));
        return;
    }
    exportRegionTopologyFromPath(utf8(sourceName));
}

std::string mcpDirectory() {
    return "/tmp/green-atlas-autocad-mcp-" +
        std::to_string(static_cast<unsigned long>(getuid()));
}

bool ensureMcpDirectory() {
    const std::string directory = mcpDirectory();
    if (mkdir(directory.c_str(), 0700) != 0 && errno != EEXIST) {
        return false;
    }
    return chmod(directory.c_str(), 0700) == 0;
}

bool writeAtomicText(const std::string& path, const std::string& payload) {
    const std::string temporary = path + ".tmp";
    std::ofstream output(temporary, std::ios::binary | std::ios::trunc);
    output << payload;
    output.close();
    if (!output || std::rename(temporary.c_str(), path.c_str()) != 0) {
        std::remove(temporary.c_str());
        return false;
    }
    return true;
}

void publishMcpStatus(const bool ready) {
    if (!ensureMcpDirectory()) return;
    const std::string payload =
        std::string("{\"schema\":\"green-atlas.autocad-mcp-status/1\",") +
        "\"ready\":" + (ready ? "true" : "false") +
        ",\"plugin_version\":\"" + kPluginVersion +
        "\",\"pid\":" + std::to_string(static_cast<long>(getpid())) + "}\n";
    writeAtomicText(mcpDirectory() + "/status.json", payload);
}

bool isMcpRequestName(const std::string& name) {
    constexpr const char* prefix = "request-";
    constexpr const char* suffix = ".txt";
    if (name.size() != 8 + 32 + 4 || name.compare(0, 8, prefix) != 0 ||
        name.compare(name.size() - 4, 4, suffix) != 0) {
        return false;
    }
    return std::all_of(
        name.begin() + 8, name.begin() + 40,
        [](const unsigned char value) {
            return (value >= '0' && value <= '9') ||
                   (value >= 'a' && value <= 'f');
        });
}

void processMcpRequestsOnIdle() {
    static auto nextPoll = std::chrono::steady_clock::now();
    const auto now = std::chrono::steady_clock::now();
    if (gMcpRequestInProgress || now < nextPoll) return;
    nextPoll = now + std::chrono::milliseconds(200);
    if (!ensureMcpDirectory()) return;

    DIR* directory = opendir(mcpDirectory().c_str());
    if (directory == nullptr) return;
    std::string requestName;
    while (const dirent* entry = readdir(directory)) {
        const std::string candidate = entry->d_name;
        if (isMcpRequestName(candidate) &&
            (requestName.empty() || candidate < requestName)) {
            requestName = candidate;
        }
    }
    closedir(directory);
    if (requestName.empty()) return;

    const std::string requestId = requestName.substr(8, 32);
    const std::string requestPath = mcpDirectory() + "/" + requestName;
    const std::string processingPath =
        mcpDirectory() + "/processing-" + requestId + ".txt";
    if (std::rename(requestPath.c_str(), processingPath.c_str()) != 0) return;

    gMcpRequestInProgress = true;
    std::ifstream request(processingPath, std::ios::binary);
    std::ostringstream requestBuffer;
    requestBuffer << request.rdbuf();
    const bool requestReadable = request.good() || request.eof();
    request.close();
    std::string sourcePath = requestBuffer.str();
    if (!sourcePath.empty() && sourcePath.back() == '\n') sourcePath.pop_back();
    if (!sourcePath.empty() && sourcePath.back() == '\r') sourcePath.pop_back();

    bool succeeded = false;
    if (!requestReadable || sourcePath.empty() || sourcePath.front() != '/' ||
        sourcePath.find('\n') != std::string::npos ||
        sourcePath.find('\r') != std::string::npos) {
        gLastTopologyExportError = "invalid MCP request payload";
    } else {
        succeeded = exportRegionTopologyFromPath(sourcePath);
    }

    std::ostringstream response;
    response << "{\"schema\":\"green-atlas.autocad-mcp-response/1\","
             << "\"request_id\":\"" << requestId << "\","
             << "\"success\":" << (succeeded ? "true" : "false") << ","
             << "\"source_path\":\"" << jsonEscape(sourcePath) << "\","
             << "\"output_path\":";
    if (succeeded) response << "\"" << jsonEscape(gLastTopologyExportPath) << "\"";
    else response << "null";
    response << ",\"error\":";
    if (succeeded) response << "null";
    else response << "\"" << jsonEscape(gLastTopologyExportError) << "\"";
    response << "}\n";
    writeAtomicText(
        mcpDirectory() + "/response-" + requestId + ".json",
        response.str());
    std::remove(processingPath.c_str());
    publishMcpStatus(true);
    gMcpRequestInProgress = false;
}

void initialize() {
    acedRegCmds->addCommand(kCommandGroup, _T("GAEXPORTPROBE"),
                            _T("GAEXPORTPROBE"), ACRX_CMD_MODAL, exportProbe);
    acedRegCmds->addCommand(kCommandGroup, _T("GAEXPORTREGIONPROBE"),
                            _T("GAEXPORTREGIONPROBE"), ACRX_CMD_MODAL,
                            exportRegionTopologyProbe);
    acedRegCmds->addCommand(kCommandGroup, _T("GAEXPORTSNAPSHOTFILE"),
                            _T("GAEXPORTSNAPSHOTFILE"), ACRX_CMD_MODAL,
                            exportRegionTopologyFromSourceFile);
    acedRegisterOnIdleWinMsg(processMcpRequestsOnIdle);
    publishMcpStatus(true);
}

void unload() {
    acedRemoveOnIdleWinMsg(processMcpRequestsOnIdle);
    publishMcpStatus(false);
    acedRegCmds->removeGroup(kCommandGroup);
}

}  // namespace

extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode message, void* appId) {
    switch (message) {
    case AcRx::kInitAppMsg:
        acrxLoadModule(_T("AcGeomentObj.dbx"), 0);
        acrxDynamicLinker->loadModule(_T("AcBr.dbx"), 1);
        acrxDynamicLinker->unlockApplication(appId);
        acrxDynamicLinker->registerAppMDIAware(appId);
        initialize();
        break;
    case AcRx::kUnloadAppMsg:
        unload();
        acrxDynamicLinker->unloadModule(_T("AcBr.dbx"));
        acrxUnloadModule(_T("AcGeomentObj.dbx"));
        break;
    default:
        break;
    }
    return AcRx::kRetOK;
}
