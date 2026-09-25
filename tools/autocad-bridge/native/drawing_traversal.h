#pragma once
#include "geometry_types.h"
#include "dbid.h"
class AcGeMatrix3d;
class AcDbEntity;
class AcDbBlockReference;

namespace ga::bridge {

class ExternalDatabaseCache;
void collectRegionInstances(const AcDbObjectId& recordId,
                            const AcGeMatrix3d& accumulatedTransform,
                            const std::vector<std::string>& instanceChain,
                            const std::string& inheritedLayer,
                            const std::string& layerNamespace,
                            const double tolerance,
                            std::vector<AcDbObjectId>& activeRecords,
                            std::vector<RegionTopology>& regions,
                            std::vector<NativeAreaProposal>& areaProposals,
                            std::vector<NativePath>& paths,
                            std::vector<NativePoint>& points,
                            std::vector<EntityCoverage>& coverage,
                            ExternalDatabaseCache& externalDatabases,
                            RegionTraversalDiagnostics& diagnostics);

void collectBlockInstance(AcDbBlockReference* blockReference,
                            const AcGeMatrix3d& accumulatedTransform,
                            const std::vector<std::string>& instanceChain,
                            const std::string& inheritedLayer,
                            const std::string& layerNamespace,
                            const double tolerance,
                            std::vector<AcDbObjectId>& activeRecords,
                            std::vector<RegionTopology>& regions,
                            std::vector<NativeAreaProposal>& areaProposals,
                            std::vector<NativePath>& paths,
                            std::vector<NativePoint>& points,
                            std::vector<EntityCoverage>& coverage,
                            ExternalDatabaseCache& externalDatabases,
                            RegionTraversalDiagnostics& diagnostics);

void collectEntityGeometry(AcDbEntity* entity,
    const AcGeMatrix3d& accumulatedTransform,
    const std::vector<std::string>& instanceChain,
    const std::string& inheritedLayer, const std::string& layerNamespace,
    double tolerance, std::vector<RegionTopology>& regions,
    std::vector<NativePath>& paths, std::vector<NativePoint>& points,
    std::vector<EntityCoverage>& coverage);

std::string effectiveEntityLayer(const std::string& sourceLayer,
                                 const std::string& inheritedLayer,
                                 const std::string& layerNamespace);
bool objectIdIn(const std::vector<AcDbObjectId>& values, const AcDbObjectId& candidate);

}  // namespace ga::bridge
