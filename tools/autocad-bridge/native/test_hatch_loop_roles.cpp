#include "hatch_loop_roles.h"
#include <cassert>
#include <cmath>
#include <string>
#include <vector>

using ga::bridge::HatchFillStyle;
using ga::bridge::Point3;
using ga::bridge::RegionLoop;
using ga::bridge::classifyHatchLoops;
using ga::bridge::hatchLoopArea;

namespace {

RegionLoop square(double x0, double y0, double x1, double y1) {
    RegionLoop loop;
    loop.coordinates = {
        Point3{x0, y0, 0}, Point3{x1, y0, 0}, Point3{x1, y1, 0},
        Point3{x0, y1, 0}, Point3{x0, y0, 0},
    };
    return loop;
}

void expectStyle(HatchFillStyle style, double expectedArea,
                 const std::vector<std::string>& expectedRoles) {
    // Order is deliberately mixed: two disjoint outer patches, a hole,
    // and an island nested inside the hole.
    std::vector<RegionLoop> loops = {
        square(2, 2, 6, 6), square(20, 0, 24, 4),
        square(0, 0, 10, 10), square(3, 3, 4, 4),
    };
    double area = 0;
    double perimeter = 0;
    std::string reason;
    assert(classifyHatchLoops(loops, style, 0.001, area, perimeter, reason));
    assert(reason.empty());
    assert(std::abs(area - expectedArea) < 1e-9);
    assert(loops.size() == expectedRoles.size());
    for (std::size_t index = 0; index < loops.size(); ++index)
        assert(loops[index].role == expectedRoles[index]);
    assert(perimeter > 0);
}

}  // namespace

int main() {
    expectStyle(HatchFillStyle::Normal, 101,
                {"hole", "outer", "outer", "outer"});
    expectStyle(HatchFillStyle::Outer, 100,
                {"hole", "outer", "outer"});
    expectStyle(HatchFillStyle::Ignore, 116,
                {"outer", "outer"});

    std::vector<RegionLoop> overlapping = {
        square(0, 0, 10, 10), square(5, 5, 9, 15),
    };
    double area = 0;
    double perimeter = 0;
    std::string reason;
    assert(!classifyHatchLoops(overlapping, HatchFillStyle::Normal,
                               0.001, area, perimeter, reason));
    assert(reason.find("overlap") != std::string::npos);

    std::vector<RegionLoop> cancelled = {square(0, 0, 10, 10)};
    assert(!classifyHatchLoops(cancelled, HatchFillStyle::Normal,
                               0.001, area, perimeter, reason,
                               [] { return true; }));
    assert(reason.find("cancelled") != std::string::npos);

    std::vector<RegionLoop> open = {square(0, 0, 10, 10)};
    open.front().coordinates.pop_back();
    assert(!classifyHatchLoops(open, HatchFillStyle::Normal,
                               0.001, area, perimeter, reason));
    assert(reason.find("not closed") != std::string::npos);

    const auto offset = square(700000, 5000000, 700001, 5000001);
    assert(std::abs(hatchLoopArea(offset.coordinates) - 1) < 1e-9);
}
