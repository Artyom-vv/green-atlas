#include "hatch_loop_roles.h"
#include "geometry_math.h"
#include <algorithm>
#include <cmath>
#include <utility>

namespace ga::bridge {
namespace {

bool pointOnSegment(const Point3& point, const Point3& left,
                    const Point3& right, double tolerance) {
    const double cross = (right.x - left.x) * (point.y - left.y) -
                         (right.y - left.y) * (point.x - left.x);
    if (std::abs(cross) > tolerance * std::max(1.0, pointDistance(left, right)))
        return false;
    return point.x >= std::min(left.x, right.x) - tolerance &&
           point.x <= std::max(left.x, right.x) + tolerance &&
           point.y >= std::min(left.y, right.y) - tolerance &&
           point.y <= std::max(left.y, right.y) + tolerance;
}

bool loopContainsPoint(const std::vector<Point3>& loop,
                       const Point3& point, double tolerance) {
    bool inside = false;
    for (std::size_t index = 1; index < loop.size(); ++index) {
        const Point3& left = loop[index - 1];
        const Point3& right = loop[index];
        if (pointOnSegment(point, left, right, tolerance)) return true;
        const bool crosses = (left.y > point.y) != (right.y > point.y);
        if (crosses && point.x <
            (right.x - left.x) * (point.y - left.y) /
                (right.y - left.y) + left.x) inside = !inside;
    }
    return inside;
}

double loopPerimeter(const std::vector<Point3>& loop) {
    double perimeter = 0.0;
    for (std::size_t index = 1; index < loop.size(); ++index)
        perimeter += pointDistance(loop[index - 1], loop[index]);
    return perimeter;
}

}  // namespace

double hatchLoopArea(const std::vector<Point3>& loop) {
    if (loop.size() < 4) return 0.0;
    // Translating to the first vertex avoids cancellation in city-coordinate
    // drawings where the occupied patch is tiny relative to WCS offsets.
    const Point3& origin = loop.front();
    double twiceArea = 0.0;
    for (std::size_t index = 1; index < loop.size(); ++index) {
        const Point3& left = loop[index - 1];
        const Point3& right = loop[index];
        twiceArea += (left.x - origin.x) * (right.y - origin.y) -
                     (right.x - origin.x) * (left.y - origin.y);
    }
    return std::abs(twiceArea) * 0.5;
}

bool classifyHatchLoops(std::vector<RegionLoop>& loops,
                        HatchFillStyle style, double tolerance,
                        double& area, double& perimeter, std::string& reason,
                        const std::function<bool()>& cancelled) {
    if (loops.empty() || !std::isfinite(tolerance) || tolerance <= 0.0) {
        reason = "HATCH has no valid typed loops";
        return false;
    }
    std::vector<double> areas;
    areas.reserve(loops.size());
    for (const auto& loop : loops) {
        if (loop.coordinates.size() < 4 ||
            loop.coordinates.front().x != loop.coordinates.back().x ||
            loop.coordinates.front().y != loop.coordinates.back().y ||
            loop.coordinates.front().z != loop.coordinates.back().z) {
            reason = "HATCH typed boundary is not closed";
            return false;
        }
        const double measured = hatchLoopArea(loop.coordinates);
        if (!std::isfinite(measured) || measured <= tolerance * tolerance) {
            reason = "HATCH typed boundary has no usable projected area";
            return false;
        }
        areas.push_back(measured);
    }
    std::vector<int> depths(loops.size(), 0);
    for (std::size_t index = 0; index < loops.size(); ++index) {
        if (cancelled && cancelled()) {
            reason = "HATCH classification cancelled";
            return false;
        }
        const auto& candidate = loops[index].coordinates;
        const std::size_t vertexCount = candidate.size() - 1;
        for (std::size_t parent = 0; parent < loops.size(); ++parent) {
            if (parent == index || areas[parent] <= areas[index]) continue;
            const auto& parentLoop = loops[parent].coordinates;
            std::size_t inside = 0;
            for (std::size_t vertex = 0; vertex < vertexCount; ++vertex) {
                if (loopContainsPoint(parentLoop, candidate[vertex], tolerance))
                    ++inside;
            }
            if (inside != 0 && inside != vertexCount) {
                reason = "HATCH loops overlap without a complete containment";
                return false;
            }
            if (inside == vertexCount) ++depths[index];
        }
    }
    std::vector<RegionLoop> filled;
    filled.reserve(loops.size());
    area = 0.0;
    perimeter = 0.0;
    int outerCount = 0;
    for (std::size_t index = 0; index < loops.size(); ++index) {
        const int depth = depths[index];
        if (style == HatchFillStyle::Outer && depth > 1) continue;
        if (style == HatchFillStyle::Ignore && depth > 0) continue;
        const bool outer = depth % 2 == 0;
        if (outer) ++outerCount;
        loops[index].role = outer ? "outer" : "hole";
        area += outer ? areas[index] : -areas[index];
        perimeter += loopPerimeter(loops[index].coordinates);
        filled.push_back(std::move(loops[index]));
    }
    if (outerCount < 1 || !std::isfinite(area) || area <= 0.0 ||
        !std::isfinite(perimeter) || perimeter <= 0.0) {
        reason = "HATCH loops do not form a positive filled surface";
        return false;
    }
    loops = std::move(filled);
    reason.clear();
    return true;
}

}  // namespace ga::bridge
