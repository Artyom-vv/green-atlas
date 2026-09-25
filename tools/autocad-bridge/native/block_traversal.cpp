#include "drawing_traversal.h"
#include "xref_resolver.h"
#include "cad_utils.h"
#include "dbents.h"
#include "dbsymtb.h"
#include "gemat3d.h"
#include <cmath>

namespace ga::bridge {

namespace {
std::string mInsertCellToken(const std::string& handle,
                             const Adesk::UInt16 row,
                             const Adesk::UInt16 column) {
    return "MINSERT:" + handle + ":R" + std::to_string(row) +
        ":C" + std::to_string(column);
}

AcGeVector3d mInsertCellOffset(const AcDbMInsertBlock* block,
                               const Adesk::UInt16 row,
                               const Adesk::UInt16 column) {
    AcGeVector3d offset(
        static_cast<double>(column) * block->columnSpacing(),
        static_cast<double>(row) * block->rowSpacing(),
        0.0);
    offset.transformBy(AcGeMatrix3d::rotation(
        block->rotation(), AcGeVector3d::kZAxis));
    offset.transformBy(AcGeMatrix3d::planeToWorld(block->normal()));
    return offset;
}

void collectAttachedAttributes(AcDbBlockReference* blockReference,
                               const std::vector<std::string>& instanceChain,
                               const std::string& effectiveLayer,
                               const std::string& layerNamespace,
                               std::vector<EntityCoverage>& coverage,
                               RegionTraversalDiagnostics& diagnostics) {
    AcDbObjectIterator* attributeIterator = blockReference->attributeIterator();
    if (attributeIterator == nullptr) return;
    for (attributeIterator->start(); !attributeIterator->done();
         attributeIterator->step()) {
        AcDbEntity* attribute = nullptr;
        if (acdbOpenObject(attribute, attributeIterator->objectId(),
                           AcDb::kForRead) != Acad::eOk ||
            attribute == nullptr) {
            ++diagnostics.unreadableEntities;
            continue;
        }
        EntityCoverage attributeRecord;
        attributeRecord.handle = entityHandle(attribute);
        attributeRecord.entityType = utf8(attribute->isA()->name());
        attributeRecord.sourceLayer = utf8(attribute->layer());
        attributeRecord.layer = attributeRecord.sourceLayer == "0"
            ? effectiveLayer
            : (layerNamespace.empty() ||
               attributeRecord.sourceLayer.rfind(layerNamespace + "|", 0) == 0
                ? attributeRecord.sourceLayer
                : layerNamespace + "|" + attributeRecord.sourceLayer);
        attributeRecord.instanceChain = instanceChain;
        attributeRecord.status = "context";
        attributeRecord.method = "autodesk-non-calculation-context";
        attributeRecord.reason =
            "attribute instance is preserved as non-calculation annotation context";
        coverage.push_back(std::move(attributeRecord));
        attribute->close();
    }
    delete attributeIterator;
}

}  // namespace

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
                            RegionTraversalDiagnostics& diagnostics) {
    AcDbEntity* entity = blockReference;

    ++diagnostics.blockReferences;
    const AcDbObjectId nestedRecordId = blockReference->blockTableRecord();
    const AcGeMatrix3d blockTransform = blockReference->blockTransform();
    const AcGeMatrix3d nestedTransform =
        accumulatedTransform * blockTransform;
    std::vector<std::string> nestedChain(instanceChain);
    nestedChain.push_back(entityHandle(blockReference));
    const std::string sourceLayer = utf8(blockReference->layer());
    const std::string effectiveLayer = effectiveEntityLayer(
        sourceLayer, inheritedLayer, layerNamespace);
    EntityCoverage record;
    record.handle = entityHandle(blockReference);
    record.entityType = utf8(entity->isA()->name());
    record.sourceLayer = sourceLayer;
    record.layer = effectiveLayer;
    record.instanceChain = instanceChain;
    AcDbMInsertBlock* mInsert = AcDbMInsertBlock::cast(blockReference);
    const bool isMInsert = mInsert != nullptr;
    const bool isInvalidMInsert = isMInsert &&
        (mInsert->rows() == 0 || mInsert->columns() == 0 ||
         !std::isfinite(mInsert->rowSpacing()) ||
         !std::isfinite(mInsert->columnSpacing()));
    bool isXref = false;
    bool isUnloadedXref = false;
    bool isUnresolvedXref = false;
    bool traversesExternalDatabase = false;
    std::string nestedLayerNamespace = layerNamespace;
    std::string xrefDependencyId;
    std::string xrefTraversalFailure;
    AcDbObjectId traversalRecordId = nestedRecordId;
    int xrefStatus = static_cast<int>(AcDb::kXrfNotAnXref);
    AcDbBlockTableRecord* nestedRecord = nullptr;
    if (acdbOpenObject(nestedRecord, nestedRecordId, AcDb::kForRead) == Acad::eOk &&
        nestedRecord != nullptr) {
        isXref = nestedRecord->isFromExternalReference();
        if (isXref) {
            const AcDb::XrefStatus status = nestedRecord->xrefStatus();
            xrefStatus = static_cast<int>(status);
            isUnloadedXref =
                nestedRecord->isUnloaded() || status == AcDb::kXrfUnloaded;
            isUnresolvedXref = status != AcDb::kXrfResolved;
            XrefDependency dependency = inspectXrefDependency(nestedRecord);
            xrefDependencyId = "xref/" + dependency.recordHandle;
            if (isUnresolvedXref && !dependency.resolvedPath.empty() &&
                dependency.bytes > 0 && !dependency.sha256.empty() &&
                externalDatabases.modelSpace(
                    dependency.resolvedPath, traversalRecordId,
                    xrefTraversalFailure)) {
                isUnresolvedXref = false;
                isUnloadedXref = false;
                traversesExternalDatabase = true;
                nestedLayerNamespace = layerNamespace.empty()
                    ? dependency.blockName
                    : layerNamespace + "|" + dependency.blockName;
                dependency.status = "resolved";
            } else if (dependency.status != "resolved") {
                isUnresolvedXref = true;
            }
            diagnostics.xrefDependencies[dependency.recordHandle] =
                std::move(dependency);
        }
        nestedRecord->close();
    }
    if (isXref) ++diagnostics.xrefBlockReferences;
    if (isUnloadedXref) ++diagnostics.unloadedXrefBlockReferences;
    if (isUnresolvedXref) ++diagnostics.unresolvedXrefBlockReferences;
    if (isInvalidMInsert) ++diagnostics.unexpandedMInsertBlocks;
    record.status = (isInvalidMInsert || isUnresolvedXref) ? "unresolved" : "context";
    if (isInvalidMInsert) {
        record.method = "minsert-not-expanded";
    } else if (isUnresolvedXref) {
        record.method = "xref-not-resolved";
    } else if (traversesExternalDatabase) {
        record.method = "traverse-package-local-xref-database";
    } else if (isXref) {
        record.method = "traverse-xref-reference";
    } else if (isMInsert) {
        record.method = "traverse-minsert-cells";
    } else {
        record.method = "traverse-block-reference";
    }
    record.reason = isInvalidMInsert
        ? "MINSERT has invalid row, column or spacing data"
        : (isUnresolvedXref
            ? "external reference could not be traversed (status=" +
                std::to_string(xrefStatus) +
                (xrefTraversalFailure.empty()
                    ? std::string()
                    : ", detail=" + xrefTraversalFailure) + ")"
            : (isXref
                ? (traversesExternalDatabase
                    ? "package-local external reference opened read-only by AutoCAD and traversed with a hashed dependency"
                    : "resolved external reference traversed with a hashed dependency")
            : (isMInsert
                ? "container expanded into " +
                    std::to_string(static_cast<std::size_t>(mInsert->rows()) *
                                   static_cast<std::size_t>(mInsert->columns())) +
                    " cell instances for descendant provenance"
                : "container instance traversed for descendant provenance")));
    record.xrefDependencyId = xrefDependencyId;
    coverage.push_back(std::move(record));

    if (isInvalidMInsert || isUnresolvedXref) {
        collectAttachedAttributes(blockReference, nestedChain, effectiveLayer,
                                  layerNamespace,
                                  coverage, diagnostics);
            return;
    }

    if (objectIdIn(activeRecords, traversalRecordId)) {
        ++diagnostics.cyclicBlockReferences;
            return;
    }
    if (isMInsert) {
        for (Adesk::UInt16 row = 0; row < mInsert->rows(); ++row) {
            for (Adesk::UInt16 column = 0; column < mInsert->columns(); ++column) {
                std::vector<std::string> cellChain(instanceChain);
                cellChain.push_back(mInsertCellToken(
                    entityHandle(blockReference), row, column));
                const AcGeMatrix3d cellTransform = accumulatedTransform *
                    AcGeMatrix3d::translation(
                        mInsertCellOffset(mInsert, row, column)) *
                    blockTransform;
                collectAttachedAttributes(blockReference, cellChain,
                                          effectiveLayer, layerNamespace, coverage,
                                          diagnostics);
                ++diagnostics.expandedMInsertCells;
                ++diagnostics.traversedBlockReferences;
                collectRegionInstances(traversalRecordId, cellTransform, cellChain,
                                       effectiveLayer, nestedLayerNamespace,
                                       tolerance, activeRecords,
                                       regions, areaProposals, paths, points, coverage,
                                       externalDatabases,
                                       diagnostics);
            }
        }
    } else {
        collectAttachedAttributes(blockReference, nestedChain, effectiveLayer,
                                  layerNamespace,
                                  coverage, diagnostics);
        ++diagnostics.traversedBlockReferences;
        collectRegionInstances(traversalRecordId, nestedTransform, nestedChain,
                               effectiveLayer, nestedLayerNamespace,
                               tolerance, activeRecords,
                               regions, areaProposals, paths, points, coverage,
                               externalDatabases,
                               diagnostics);
    }
    return;

}

}  // namespace ga::bridge
