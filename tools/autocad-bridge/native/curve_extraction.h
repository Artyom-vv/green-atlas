#pragma once
#include "geometry_types.h"
class AcDbPolyline;
class AcDbSolid;
class AcDbMline;
class AcDbCurve;
class AcGeMatrix3d;

namespace ga::bridge {

bool extractLightweightPolyline(const AcDbPolyline* polyline,
                                const AcGeMatrix3d& transform,
                                const double tolerance,
                                NativePath& result);
bool extractSolidBoundary(const AcDbSolid* solid,
                          const AcGeMatrix3d& transform,
                          const double tolerance,
                          NativePath& result);
bool extractMlineAxis(const AcDbMline* mline,
                      const AcGeMatrix3d& transform,
                      const double tolerance,
                      NativePath& result);
bool extractDatabaseCurve(const AcDbCurve* curve,
                          const AcGeMatrix3d& transform,
                          const double tolerance,
                          NativePath& result);

}  // namespace ga::bridge
