#pragma once
#include "direct_query_kernel.h"
#include <functional>

namespace ga::clearance {
// All lengths are WORLD drawing units, supplied by the rule/units adapter.
// No planning defaults or sampled CAD geometry belong in this module.
using Region = std::unique_ptr<AcDbRegion>;
using Cancel = std::function<bool()>;
inline constexpr std::size_t kMaximumBoundaryPieces = 16384;
inline constexpr double kPlanarTolerance = 1e-8;
struct Mask {
    Region region;
    std::size_t boundaryPieces = 0;
};
Region cloneRegion(const AcDbRegion& source);
Region polygon(const std::vector<AcGePoint3d>& vertices);
// Only detached copies are modified. Unknown/unsupported pieces fail the
// WHOLE object, never publish a partially buffered obstacle as complete.
Mask aroundCurve(AcDbEntity& curve, const AcGeMatrix3d& world,
                 double distance, const Cancel& cancelled = {});
Mask aroundArea(AcDbRegion& region, const AcGeMatrix3d& world,
                double distance, const Cancel& cancelled = {});
// Empty difference is represented by nullptr, not an invalid empty BRep.
Region subtract(const AcDbRegion& work, const AcDbRegion& obstacle);
double area(const AcDbRegion& region);
}
