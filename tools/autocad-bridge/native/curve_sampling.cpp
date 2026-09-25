#include "curve_sampling.h"
#include "geometry_math.h"
#include "cad_utils.h"
#include "operation_control.h"
#include "bridge_config.h"
#include "gecurv3d.h"
#include "gemat3d.h"
#include "geintrvl.h"
#include "dbcurve.h"
#include <algorithm>
#include <cmath>

namespace ga::bridge {

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
    if (operationCancelled()) return false;
    if (output.size() >= kMaximumSampledPointsPerLoop) return false;
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
        if (output.size() >= kMaximumSampledPointsPerLoop) return false;
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
    if (output.size() >= kMaximumSampledPointsPerLoop) return false;
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
    if (operationCancelled()) return false;
    if (output.size() >= kMaximumSampledPointsPerLoop) return false;
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
        if (output.size() >= kMaximumSampledPointsPerLoop) return false;
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

}  // namespace ga::bridge
