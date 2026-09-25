#pragma once
#include "geometry_types.h"
class AcGeCurve3d;
class AcGeMatrix3d;
class AcDbCurve;

namespace ga::bridge {

bool appendSampledCurve(const AcGeCurve3d& curve,
                        const AcGeMatrix3d& transform,
                        const double tolerance,
                        std::vector<Point3>& output,
                        double& sampledMaximumDeviation);
bool sampleDatabaseCurveInterval(const AcDbCurve* curve,
                                 const AcGeMatrix3d& transform,
                                 const double startParameter,
                                 const double endParameter,
                                 const Point3& startPoint,
                                 const Point3& endPoint,
                                 const double tolerance,
                                 const int depth,
                                 std::vector<Point3>& output,
                                 double& sampledMaximumDeviation);

}  // namespace ga::bridge
