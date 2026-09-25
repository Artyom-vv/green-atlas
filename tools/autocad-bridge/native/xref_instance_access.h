#pragma once
#include "direct_query_kernel.h"
namespace ga::xref {
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
