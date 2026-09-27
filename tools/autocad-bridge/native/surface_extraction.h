#pragma once
#include "geometry_types.h"
class AcDbRegion;
class AcDbHatch;
class AcGeMatrix3d;

namespace ga::bridge {

// Similarities need only s²; anisotropic transforms require a native plane.
bool transformedAreaScale(AcDbRegion* region,
                          const AcGeMatrix3d& transform,
                          double& areaScale);

bool extractRegionTopology(AcDbRegion* region,
                           const AcGeMatrix3d& transform,
                           const double tolerance,
                           RegionTopology& result);
bool extractPolylineHatchTopology(const AcDbHatch* hatch,
                                  const AcGeMatrix3d& transform,
                                  const double tolerance,
                                  RegionTopology& result,
                                  std::string& reason);

}  // namespace ga::bridge
