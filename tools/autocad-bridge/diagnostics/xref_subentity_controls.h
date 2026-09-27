#pragma once
#include "direct_query_kernel.h"
#include <ostream>

namespace ga::xref {
// Diagnostic comparison only. Does not alter the query kernel or source DB.
void inspectSubentityPaths(const ga::direct::Prepared& prepared,
                          AcDbEntity& entity, const AcDbObjectIdArray& parents,
                          std::ostream& out);
}
