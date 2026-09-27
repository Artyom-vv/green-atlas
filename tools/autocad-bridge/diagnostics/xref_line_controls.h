#pragma once
#include "dbents.h"
#include <map>
#include <sstream>
#include <string>

namespace ga::xref {
std::string transformKind(const AcGeMatrix3d& matrix);
// Bounded measurements, not an extractor. Controls stay native AcDbLine copies.
struct LineControls {
    std::map<std::string,unsigned> counts;
    unsigned tested=0;
    std::ostringstream rows;
    void inspect(AcDbEntity& entity, const AcDbObjectIdArray& parents,
                 const AcGeMatrix3d& traversal, AcDbDatabase& host,
                 const std::string& route, const std::string& context);
};
void clipState(std::ostream& out,const AcDbBlockReference& ref);
}
