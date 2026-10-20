#pragma once
#include <string>
class AcDbDatabase;
namespace ga::queryWorker {
void prepareArchivedFaces(AcDbDatabase& database, const std::string& request,
                          bool retainCatalogue = false);
void prepareFacesPackageCommand();
}
