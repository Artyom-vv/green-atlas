#include "drawing_traversal.h"
#include "surface_extraction.h"
#include "curve_extraction.h"
#include "cad_utils.h"
#include "dbregion.h"
#include "dbhatch.h"
#include "dbmline.h"
#include "dbents.h"
#include "gemat3d.h"

namespace ga::bridge {

void collectEntityGeometry(AcDbEntity* entity,
    const AcGeMatrix3d& accumulatedTransform,
    const std::vector<std::string>& instanceChain,
    const std::string& inheritedLayer, const std::string& layerNamespace,
    double tolerance, std::vector<RegionTopology>& regions,
    std::vector<NativePath>& paths, std::vector<NativePoint>& points,
    std::vector<EntityCoverage>& coverage) {
    if (AcDbRegion* region = AcDbRegion::cast(entity)) {
        RegionTopology topology;
        topology.measurement.handle = entityHandle(region);
        topology.sourceLayer = utf8(region->layer());
        topology.measurement.layer = effectiveEntityLayer(
            topology.sourceLayer, inheritedLayer, layerNamespace);
        topology.instanceChain = instanceChain;
        extractRegionTopology(region, accumulatedTransform, tolerance, topology);
        EntityCoverage record;
        record.handle = topology.measurement.handle;
        record.entityType = utf8(entity->isA()->name());
        record.sourceLayer = topology.sourceLayer;
        record.layer = topology.measurement.layer;
        record.instanceChain = instanceChain;
        record.status = topology.resolved ? "native" : "unresolved";
        record.method = topology.resolved
            ? "autodesk-acbr-local-affine"
            : "autodesk-acbr-local-affine-failed";
        if (!topology.resolved) {
            record.reason = "native REGION topology extraction failed";
        }
        coverage.push_back(std::move(record));
        regions.push_back(std::move(topology));
    } else if (AcDbHatch* hatch = AcDbHatch::cast(entity)) {
        const std::string handle = entityHandle(hatch);
        const std::string sourceLayer = utf8(hatch->layer());
        const std::string effectiveLayer = effectiveEntityLayer(
            sourceLayer, inheritedLayer, layerNamespace);
        EntityCoverage record;
        record.handle = handle;
        record.entityType = utf8(entity->isA()->name());
        record.sourceLayer = sourceLayer;
        record.layer = effectiveLayer;
        record.instanceChain = instanceChain;
        RegionTopology topology;
        topology.measurement.handle = handle;
        topology.measurement.layer = effectiveLayer;
        topology.sourceLayer = sourceLayer;
        topology.instanceChain = instanceChain;
        std::string reason;
        extractPolylineHatchTopology(
            hatch, accumulatedTransform, tolerance, topology, reason);
        record.status = topology.resolved ? "native" : "unresolved";
        record.method = topology.resolved
            ? topology.extractionMethod
            : "autodesk-hatch-polyline-values";
        record.reason = reason;
        coverage.push_back(std::move(record));
        if (topology.resolved) regions.push_back(std::move(topology));
    } else if (AcDbMline* mline = AcDbMline::cast(entity)) {
        NativePath path;
        path.handle = entityHandle(mline);
        path.sourceLayer = utf8(mline->layer());
        path.layer = effectiveEntityLayer(
            path.sourceLayer, inheritedLayer, layerNamespace);
        path.instanceChain = instanceChain;
        extractMlineAxis(mline, accumulatedTransform, tolerance, path);
        EntityCoverage record;
        record.handle = path.handle;
        record.entityType = utf8(entity->isA()->name());
        record.sourceLayer = path.sourceLayer;
        record.layer = path.layer;
        record.instanceChain = instanceChain;
        record.status = path.resolved
            ? "native"
            : (path.calculationContext ? "context" : "unresolved");
        record.method = path.method +
            (path.resolved || path.calculationContext ? "" : "-failed");
        if (!path.resolved) {
            record.reason = path.reason.empty()
                ? "native AcDbMline axis extraction failed"
                : path.reason;
        }
        coverage.push_back(std::move(record));
        if (path.resolved) paths.push_back(std::move(path));
    } else if (AcDbSolid* solid = AcDbSolid::cast(entity)) {
        NativePath path;
        path.handle = entityHandle(solid);
        path.sourceLayer = utf8(solid->layer());
        path.layer = effectiveEntityLayer(
            path.sourceLayer, inheritedLayer, layerNamespace);
        path.instanceChain = instanceChain;
        extractSolidBoundary(solid, accumulatedTransform, tolerance, path);
        EntityCoverage record;
        record.handle = path.handle;
        record.entityType = utf8(entity->isA()->name());
        record.sourceLayer = path.sourceLayer;
        record.layer = path.layer;
        record.instanceChain = instanceChain;
        record.status = path.resolved ? "native" : (path.calculationContext ? "context" : "unresolved");
        record.method = path.method;
        record.reason = path.reason;
        coverage.push_back(std::move(record));
        if (path.resolved) paths.push_back(std::move(path));
    } else if (AcDbCurve* curve = AcDbCurve::cast(entity)) {
        NativePath path;
        path.handle = entityHandle(curve);
        path.sourceLayer = utf8(curve->layer());
        path.layer = effectiveEntityLayer(
            path.sourceLayer, inheritedLayer, layerNamespace);
        path.instanceChain = instanceChain;
        extractDatabaseCurve(curve, accumulatedTransform, tolerance, path);
        EntityCoverage record;
        record.handle = path.handle;
        record.entityType = utf8(entity->isA()->name());
        record.sourceLayer = path.sourceLayer;
        record.layer = path.layer;
        record.instanceChain = instanceChain;
        record.status = path.resolved
            ? "native"
            : (path.calculationContext ? "context" : "unresolved");
        record.method = path.method +
            (path.resolved || path.calculationContext ? "" : "-failed");
        if (!path.resolved) {
            record.reason = path.reason.empty()
                ? "native finite curve extraction failed"
                : path.reason;
        }
        coverage.push_back(std::move(record));
        if (path.resolved) paths.push_back(std::move(path));
    } else if (AcDbPoint* point = AcDbPoint::cast(entity)) {
        NativePoint nativePoint;
        nativePoint.handle = entityHandle(point);
        nativePoint.sourceLayer = utf8(point->layer());
        nativePoint.layer = effectiveEntityLayer(
            nativePoint.sourceLayer, inheritedLayer, layerNamespace);
        nativePoint.instanceChain = instanceChain;
        AcGePoint3d position = point->position();
        position.transformBy(accumulatedTransform);
        nativePoint.coordinates = point3(position);
        EntityCoverage record;
        record.handle = nativePoint.handle;
        record.entityType = utf8(entity->isA()->name());
        record.sourceLayer = nativePoint.sourceLayer;
        record.layer = nativePoint.layer;
        record.instanceChain = instanceChain;
        record.status = "native";
        record.method = "autodesk-acdbpoint-wcs";
        coverage.push_back(std::move(record));
        points.push_back(std::move(nativePoint));
    } else {
        EntityCoverage record;
        record.handle = entityHandle(entity);
        record.entityType = utf8(entity->isA()->name());
        record.sourceLayer = utf8(entity->layer());
        record.layer = effectiveEntityLayer(
            record.sourceLayer, inheritedLayer, layerNamespace);
        record.instanceChain = instanceChain;
        if (isNonCalculationContext(entity)) {
            record.status = "context";
            record.method = "autodesk-non-calculation-context";
            record.reason =
                "known annotation or presentation entity is not calculation geometry";
        } else {
            record.status = "unresolved";
            record.method = "autodesk-native-geometry-not-implemented";
            record.reason =
                "entity instance is inventoried but native geometry is not implemented";
        }
        coverage.push_back(std::move(record));
    }
}

}  // namespace ga::bridge
