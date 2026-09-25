#pragma once
#include "direct_query_kernel.h"
#include "dbcurve.h"

namespace ga::nativeQuery {
// Native world-curve XY distance. It does not establish an occupied interior.
class CurveQuery {
    std::unique_ptr<AcDbEntity> owner;
    AcDbCurve* curve = nullptr;
public:
    void prepare(AcDbEntity& entity, const AcGeMatrix3d& transform);
    ga::direct::Answer queryPlanar(const AcGePoint3d& point) const;
};
}
