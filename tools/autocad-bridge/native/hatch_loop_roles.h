#pragma once

#include "geometry_types.h"
#include <functional>
#include <string>
#include <vector>

namespace ga::bridge {

enum class HatchFillStyle { Normal, Outer, Ignore };

double hatchLoopArea(const std::vector<Point3>& loop);

// Uses only the typed coordinates emitted by AutoCAD. Does not join, repair,
// polygonize or change the source HATCH. The downstream polygon provider must
// still reject overlapping/touching loops before calculation.
bool classifyHatchLoops(std::vector<RegionLoop>& loops,
                        HatchFillStyle style,
                        double tolerance,
                        double& area,
                        double& perimeter,
                        std::string& reason,
                        const std::function<bool()>& cancelled = {});

}  // namespace ga::bridge
