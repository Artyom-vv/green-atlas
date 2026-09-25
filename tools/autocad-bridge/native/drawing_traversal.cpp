#include "drawing_traversal.h"
#include "operation_control.h"
#include "exact_polyline_pairs.h"
#include "dbents.h"
#include "dbsymtb.h"
#include <algorithm>
#include <utility>

namespace ga::bridge {

std::string effectiveEntityLayer(const std::string& sourceLayer,
                                 const std::string& inheritedLayer,
                                 const std::string& layerNamespace) {
    const std::string layer = sourceLayer == "0" ? inheritedLayer : sourceLayer;
    if (layerNamespace.empty() || layer.empty() || layer == "0" ||
        layer.rfind(layerNamespace + "|", 0) == 0) {
        return layer;
    }
    return layerNamespace + "|" + layer;
}

bool objectIdIn(const std::vector<AcDbObjectId>& values, const AcDbObjectId& candidate) {
    return std::find(values.begin(), values.end(), candidate) != values.end();
}

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
                            RegionTraversalDiagnostics& diagnostics) {
    if (recordId.isNull() || objectIdIn(activeRecords, recordId)) {
        ++diagnostics.cyclicBlockReferences;
        return;
    }
    activeRecords.push_back(recordId);

    AcDbBlockTableRecord* record = nullptr;
    if (acdbOpenObject(record, recordId, AcDb::kForRead) != Acad::eOk || record == nullptr) {
        ++diagnostics.unreadableBlockRecords;
        activeRecords.pop_back();
        return;
    }
    AcDbBlockTableRecordIterator* iterator = nullptr;
    if (record->newIterator(iterator) != Acad::eOk || iterator == nullptr) {
        ++diagnostics.unreadableBlockRecords;
        record->close();
        activeRecords.pop_back();
        return;
    }
    std::vector<NativeCurveCandidate> candidateCurves;

    for (iterator->start(); !iterator->done(); iterator->step()) {
        if (operationCancelled()) break;
        ++diagnostics.visitedEntities;
        if (diagnostics.visitedEntities % 10000 == 0) {
            reportOperationProgress("collecting", diagnostics.visitedEntities);
        }
        AcDbEntity* entity = nullptr;
        if (iterator->getEntity(entity, AcDb::kForRead) != Acad::eOk || entity == nullptr) {
            ++diagnostics.unreadableEntities;
            continue;
        }


        if (AcDbBlockReference* block = AcDbBlockReference::cast(entity)) {
            collectBlockInstance(block, accumulatedTransform, instanceChain,
                                 inheritedLayer, layerNamespace, tolerance,
                                 activeRecords, regions, areaProposals, paths, points, coverage,
                                 externalDatabases, diagnostics);
        } else {
            NativeCurveCandidate candidate;
            const bool candidateReady = inspectNativeCurveCandidate(entity, candidate);
            const std::size_t coverageBefore = coverage.size();
            const std::size_t pathsBefore = paths.size();
            collectEntityGeometry(entity, accumulatedTransform, instanceChain,
                                  inheritedLayer, layerNamespace, tolerance,
                                  regions, paths, points, coverage);
            if (candidateReady) {
                candidate.pathResolved = coverage.size() == coverageBefore + 1 &&
                    coverage.back().status == "native" &&
                    paths.size() == pathsBefore + 1 &&
                    paths.back().handle == candidate.handle &&
                    !paths.back().closed;
                candidateCurves.push_back(std::move(candidate));
            }
        }
        entity->close();
    }

    if (!operationCancelled()) {
        collectExactPolylinePairs(candidateCurves, accumulatedTransform,
                                  instanceChain, inheritedLayer, layerNamespace,
                                  tolerance, regions);
        collectNearClosedAreaProposals(candidateCurves, accumulatedTransform,
                                       instanceChain, inheritedLayer, layerNamespace,
                                       tolerance, regions, areaProposals, diagnostics);
    }

    delete iterator;
    record->close();
    activeRecords.pop_back();
}

}  // namespace ga::bridge
