#pragma once
#include "geometry_types.h"
#include "dbsymtb.h"
#include <memory>

namespace ga::bridge {

class ExternalDatabaseCache {
public:
    bool modelSpace(const std::string& path, AcDbObjectId& modelSpaceId,
                    std::string& reason);
private:
    std::map<std::string, std::unique_ptr<AcDbDatabase>> databases;
};

XrefDependency inspectXrefDependency(AcDbBlockTableRecord* record);
AcDbObjectIdArray relinkUniquePackageLocalXrefs(AcDbDatabase* database,
                                              const std::string& sourcePath);

}  // namespace ga::bridge
