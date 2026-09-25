#include "capture_commands.h"
#include "exact_polyline_pairs.h"
#include "curve_extraction.h"
#include "surface_extraction.h"
#include "geometry_math.h"
#include "file_io.h"
#include "mcp_transport.h"
#include "dbpl.h"
#include "dbents.h"
#include "dbhatch.h"
#include "dbregion.h"
#include "gemat3d.h"
#include "adslib.h"
#include <cmath>
#include <sstream>

namespace ga::bridge {

#ifdef GA_GEOMETRY_SELFTEST
// Compiled only in the developer qualification bundle. Uses the real Autodesk
// classes in memory, never the user's database and never writes a drawing.
void geometrySelftest() {
    std::vector<std::string> failed;
    int checks = 0;
    auto check = [&](bool success, const char* name) {
        ++checks;
        if (!success) failed.push_back(name);
    };
    const double tolerance = 0.0001;
    auto area = [](const std::vector<Point3>& points) {
        double twice = 0;
        for (size_t i = 1; i < points.size(); ++i)
            twice += points[i - 1].x * points[i].y - points[i].x * points[i - 1].y;
        return std::abs(twice) * 0.5;
    };
    AcGeMatrix3d identity;
    check(nativeEndpointPartnersSelftest(),
          "native numeric endpoints join, real gaps/branches/different layers do not");
    AcDbRegion emptyRegion;
    double scale = 0.0;
    check(transformedAreaScale(&emptyRegion, identity, scale) && scale == 1.0,
          "identity area scale does not require a single region normal");
    AcGeMatrix3d uniform;
    uniform.setToScaling(-2.0);
    check(transformedAreaScale(&emptyRegion, uniform, scale) && scale == 4.0,
          "reflected similarity transforms scale native area by s squared");
    AcGeMatrix3d nonuniform;
    nonuniform(0, 0) = 2.0;
    check(transformedAreaScale(&emptyRegion, nonuniform, scale) && scale == 2.0,
          "nonuniform area transform uses the native plane");
    nonuniform(0, 0) = 0.0;
    check(!transformedAreaScale(&emptyRegion, nonuniform, scale),
          "singular area transform remains invalid");
    for (const bool closed : {false, true}) {
        AcDbPolyline zero;
        zero.addVertexAt(0, AcGePoint2d(11465.28910389395, -8073.669212301757));
        zero.addVertexAt(1, AcGePoint2d(11465.28910389395, -8073.669212301757));
        zero.setClosed(closed);
        NativePath result;
        check(!extractLightweightPolyline(&zero, identity, tolerance, result)
              && result.calculationContext, "coincident polyline is context");
    }
    AcDbPolyline retraced;
    retraced.addVertexAt(0, AcGePoint2d(0, 0));
    retraced.addVertexAt(1, AcGePoint2d(4, 3));
    retraced.setClosed(true);
    NativePath line;
    check(extractLightweightPolyline(&retraced, identity, tolerance, line)
          && !line.closed && line.coordinates.size() == 2
          && std::abs(pointDistance(line.coordinates[0], line.coordinates[1]) - 5) < 1e-9,
          "retraced line keeps exact geometry");
    for (const double bulge : {1.0, -1.0}) {
        AcDbPolyline circle;
        circle.addVertexAt(0, AcGePoint2d(0, 0), bulge);
        circle.addVertexAt(1, AcGePoint2d(2, 0), bulge);
        circle.setClosed(true);
        NativePath result;
        check(extractLightweightPolyline(&circle, identity, tolerance, result)
              && result.closed && result.coordinates.size() > 4,
              "two arc vertices remain an area");
    }
    AcDbSolid quad(AcGePoint3d(0,0,0), AcGePoint3d(4,0,0),
                   AcGePoint3d(0,3,0), AcGePoint3d(4,3,0));
    NativePath rectangle;
    check(extractSolidBoundary(&quad, identity, tolerance, rectangle)
          && rectangle.closed && rectangle.coordinates.size() == 5
          && std::abs(area(rectangle.coordinates) - 12) < 1e-9,
          "SOLID perimeter is not a bow-tie");
    AcDbSolid triangle(AcGePoint3d(0,0,0), AcGePoint3d(4,0,0), AcGePoint3d(0,3,0));
    NativePath tri;
    check(extractSolidBoundary(&triangle, identity, tolerance, tri)
          && tri.coordinates.size() == 4
          && std::abs(area(tri.coordinates) - 6) < 1e-9,
          "triangular SOLID removes duplicate corner");
    AcGeMatrix3d transform;
    transform.setToScaling(-2.0);
    transform.setTranslation(AcGeVector3d(12000, -8000, 0));
    NativePath mirrored;
    check(extractSolidBoundary(&quad, transform, tolerance, mirrored)
          && std::abs(area(mirrored.coordinates) - 48) < 1e-9
          && pointDistance(mirrored.coordinates.front(), {12000, -8000, 0}) < 1e-9,
          "nested affine transform applies to SOLID");
    std::ostringstream report;
    AcDbHatch hatch;
    hatch.setNormal(AcGeVector3d::kZAxis);
    hatch.setElevation(0);
    AcGePoint2dArray vertices;
    for (auto point : {AcGePoint2d(0,0), AcGePoint2d(4,0), AcGePoint2d(4,3), AcGePoint2d(0,3)})
        vertices.append(point);
    AcGeDoubleArray bulges;
    check(hatch.appendLoop(AcDbHatch::kExternal | AcDbHatch::kPolyline, vertices, bulges) == Acad::eOk,
          "construct native polyline HATCH");
    RegionTopology hatchPath;
    std::string hatchReason;
    check(extractPolylineHatchTopology(&hatch, identity, tolerance, hatchPath, hatchReason)
          && hatchPath.loops.size() == 1
          && std::abs(hatchPath.measurement.area - 12) < 1e-9,
          "extract typed HATCH value arrays");
    AcGePoint2dArray hole;
    for (auto point : {AcGePoint2d(1,1), AcGePoint2d(2,1), AcGePoint2d(2,2), AcGePoint2d(1,2)})
        hole.append(point);
    hatch.appendLoop(AcDbHatch::kPolyline, hole, bulges);
    RegionTopology withHole;
    std::string holeReason;
    check(extractPolylineHatchTopology(&hatch, identity, tolerance, withHole, holeReason)
          && withHole.resolved && withHole.loops.size() == 2
          && std::abs(withHole.measurement.area - 11) < 1e-9,
          "polyline HATCH preserves islands");
    AcDbHatch disjoint;
    disjoint.setNormal(AcGeVector3d::kZAxis);
    disjoint.setElevation(0);
    AcGePoint2dArray separate;
    for (auto point : {AcGePoint2d(20,0), AcGePoint2d(24,0),
                       AcGePoint2d(24,3), AcGePoint2d(20,3)})
        separate.append(point);
    const auto firstLoop = disjoint.appendLoop(
        AcDbHatch::kExternal, vertices, bulges);
    const auto secondLoop = disjoint.appendLoop(
        AcDbHatch::kExternal, separate, bulges);
    RegionTopology twoSurfaces;
    std::string twoSurfacesReason;
    check(firstLoop == Acad::eOk && secondLoop == Acad::eOk &&
          extractPolylineHatchTopology(
              &disjoint, identity, tolerance, twoSurfaces, twoSurfacesReason) &&
          twoSurfaces.loops.size() == 2 &&
          twoSurfaces.loops[0].role == "outer" &&
          twoSurfaces.loops[1].role == "outer" &&
          std::abs(twoSurfaces.measurement.area - 24) < 1e-9,
          "disjoint typed HATCH retains both native surfaces");
    report << "{\"checks\":" << checks << ",\"failed\":[";
    for (size_t i = 0; i < failed.size(); ++i) {
        if (i) report << ',';
        report << '\"' << jsonEscape(failed[i]) << '\"';
    }
    report << "]}\n";
    writeAtomicText(mcpDirectory() + "/geometry-selftest.json", report.str());
    acutPrintf(_T("\nGreen Atlas geometry self-test: %d/%d passed."),
               checks - static_cast<int>(failed.size()), checks);
}

#endif

}  // namespace ga::bridge
