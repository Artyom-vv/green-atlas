#pragma once
#include "geometry_types.h"

namespace ga::bridge {

bool samplingBudgetExhausted(const std::vector<Point3>& coordinates);
double pointDistance(const Point3& left, const Point3& right);
double projectedPointDistance(const Point3& left, const Point3& right);
bool finitePoint(const Point3& point);
double pointSegmentDistance(const Point3& point, const Point3& start, const Point3& end);

}  // namespace ga::bridge
