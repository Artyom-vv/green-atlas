#pragma once
#include "dbents.h"
#include <map>
#include <sstream>
#include <string>
#include <vector>
namespace ga::xref {
struct AreaControl { AcGePoint3d point; std::string expected,label; };
struct AreaControls {
    bool enabled=false;
    std::string progressPath;
    std::map<std::string,std::vector<AreaControl>> explicitCases;
    std::map<std::string,unsigned> populations;
    std::ostringstream rows;
    unsigned tested=0,automatic=0;
    void read(std::istream& in);
    void inspect(AcDbEntity& entity,const AcDbObjectIdArray& parents,
                 const AcGeMatrix3d& traversal,AcDbDatabase& host,
                 const std::string& route,const std::string& context);
    void finish(std::ostream& out);
};
}
