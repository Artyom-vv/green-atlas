#pragma once
#include "dbents.h"
#include <memory>
#include <string>
#include <istream>
#include <ostream>
namespace ga::xref {
class WindowQueries {
    struct Impl;
    std::unique_ptr<Impl> impl;
public:
    WindowQueries();
    ~WindowQueries();
    void read(std::istream& in,double x0,double y0,double x1,double y1,double padding,
              bool explicitPoints=false);
    const std::string& pinnedSiteRoute() const;
    void collectAreaCandidate(AcDbEntity& entity, const std::string& route,
                              const std::string& effectiveLayer);
    void inspect(AcDbEntity& entity,const AcGeMatrix3d& transform,
                 const std::string& route,const std::string& effectiveLayer,
                 const AcGePoint3d& lo,const AcGePoint3d& hi);
    void write(std::ostream& out);
};
}
