#include "drawing_traversal.h"
#include "cad_utils.h"
#include "instance_layer.h"
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
    return resolveInstanceLayer(sourceLayer, inheritedLayer, layerNamespace);
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
                            RegionTraversalDiagnostics& diagnostics,
                            const bool annotationContext) {
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
                                 externalDatabases, diagnostics, annotationContext);
        } else if (annotationContext) {
            // Графика внутри таблицы остаётся в полном перечне и CAD-архиве,
            // но не создаёт расчётные контуры, точки или предложения областей.
            EntityCoverage row;
            row.handle = entityHandle(entity);
            row.entityType = utf8(entity->isA()->name());
            row.sourceLayer = utf8(entity->layer());
            row.layer = effectiveEntityLayer(row.sourceLayer, inheritedLayer, layerNamespace);
            row.instanceChain = instanceChain;
            row.status = "context";
            row.method = "autodesk-annotation-owner-context";
            row.reason = "entity belongs to a non-calculation annotation owner";
            coverage.push_back(std::move(row));
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

    if (!annotationContext && !operationCancelled()) {
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
