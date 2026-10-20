#pragma once
#include "semantic_evidence.h"
#include <memory>
class AcDbEntity;
class AcGeMatrix3d;

namespace ga::capture {
struct SemanticReadBudget {
    std::size_t eligibleCount = 0, retainedValueBytes = 0;
};
bool semanticEligible(const AcDbEntity& entity);
std::unique_ptr<SemanticValue> readSemanticValue(AcDbEntity& entity,
                                              const AcGeMatrix3d& transform,
                                              SemanticReadBudget& budget);
}
