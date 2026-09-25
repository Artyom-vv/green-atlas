#include "geometry_math.h"
#include "bridge_config.h"
#include <algorithm>
#include <cmath>

namespace ga::bridge {

bool samplingBudgetExhausted(const std::vector<Point3>& coordinates) {
    return coordinates.size() >= kMaximumSampledPointsPerLoop;
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

}  // namespace ga::bridge
