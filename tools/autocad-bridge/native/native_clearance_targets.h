#pragma once
#include "native_clearance.h"
#include "native_query_batch.h"

namespace ga::clearance {
// Same explicit target semantics as direct measurements, including reviewed
// closures/groups and catalogued composite faces. No automatic area downgrade.
Mask prepareTarget(AcDbDatabase& database,const ga::nativeQuery::ObjectTarget& target,
                   double distance,const Cancel& cancelled = {});
}
