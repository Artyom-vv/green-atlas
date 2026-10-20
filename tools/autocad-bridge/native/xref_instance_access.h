#pragma once
#include "direct_query_kernel.h"
#include <stdexcept>
namespace ga::xref {
class NonCalculationContext final : public std::runtime_error {
public:
    NonCalculationContext():std::runtime_error("native annotation ancestor is outside calculation") {}
};
struct Instance {
    ga::direct::ReadEntities opened;
    AcDbEntity* entity=nullptr;
    AcDbObjectIdArray parents;
    AcGeMatrix3d transform;
    double compoundMatrixDelta=0;
};
// Route is parent-handle/parent-handle/entity-handle, not a global handle.
// Only resolved references from the loaded host are traversed; no file fallback.
void resolve(AcDbDatabase& host,const std::string& route,Instance& result);
}
