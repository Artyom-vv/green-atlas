#pragma once
#include "xref_dependency.h"
#include "dbsymtb.h"
#include <memory>

namespace ga::bridge {

class ExternalDatabaseCache {
public:
    explicit ExternalDatabaseCache(bool allowFileReads = true) : allowFileReads_(allowFileReads) {}
    bool modelSpace(const std::string& path, AcDbObjectId& modelSpaceId,
                    std::string& reason);
private:
    bool allowFileReads_;
    std::map<std::string, std::unique_ptr<AcDbDatabase>> databases;
};

AcDbObjectIdArray relinkUniquePackageLocalXrefs(AcDbDatabase* database,
                                              const std::string& sourcePath);

}  // namespace ga::bridge
