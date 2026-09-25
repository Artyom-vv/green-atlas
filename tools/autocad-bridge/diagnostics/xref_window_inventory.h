#pragma once
#include "dbents.h"
#include "xref_window_queries.h"
#include <map>
#include <sstream>
#include <string>

namespace ga::xref {
// Broad phase only: world AABBs from native extents. Never geometry for planting.
struct WindowInventory {
    bool enabled=false;
    double minX=0,minY=0,maxX=0,maxY=0,padding=0;
    unsigned inspected=0,near=0,outside=0,unlocated=0,containers=0;
    std::ostringstream rows,failures;
    std::map<std::string,unsigned> layerTypes;
    std::unique_ptr<WindowQueries> queries;
    void read(std::istream& input);
    void inspect(AcDbEntity& entity,const AcGeMatrix3d& transform,
                 const std::string& route,const std::string& context,
                 const std::string& inheritedLayer);
    void write(std::ostream& out) const;
};
}
