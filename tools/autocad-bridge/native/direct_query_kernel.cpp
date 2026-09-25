#include "direct_query_kernel.h"
#include "AcString.h"
#include "dbents.h"
#include "dbcurve.h"
#include "dbhatch.h"
#include "dbsubeid.h"
#include "brbetrav.h"
#include "bredge.h"
#include "geintrvl.h"
#include "geponc3d.h"
#include "geextc3d.h"
#include "getol.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace ga::direct {
namespace {
constexpr unsigned kEdgeBudget = 16384;
constexpr double kQueryTolerance = 1e-8; // Native query tolerance, not tessellation.
bool finite(const AcGePoint3d& p) {
    return std::isfinite(p.x) && std::isfinite(p.y) && std::isfinite(p.z);
}
AcDbEntity* open(AcDbDatabase& db, const std::string& handle, ReadEntities& owner) {
    AcDbObjectId id;
    auto status = db.getAcDbObjectId(id, false, AcDbHandle(AcString(handle.c_str()).kwszPtr()));
    AcDbEntity* entity = nullptr;
    if (status == Acad::eOk) status = acdbOpenObject(entity, id, AcDb::kForRead);
    if (status != Acad::eOk || !entity)
        throw std::runtime_error("open " + handle + " status " + std::to_string(int(status)));
    owner.values.push_back(entity);
    return entity;
}
void require(AcBr::ErrorStatus status, const char* operation) {
    if (status != AcBr::eOk)
        throw std::runtime_error(std::string(operation) + " status " + std::to_string(int(status)));
}
}

void prepare(AcDbDatabase& db, const std::string& handle,
             const std::vector<std::string>& chain, Prepared& result) {
    const auto start = std::chrono::steady_clock::now();
    AcDbObjectIdArray ids;
    AcDbObjectId expectedOwner;
    for (const auto& name : chain) {
        auto* entity = open(db, name, result.opened);
        auto* reference = AcDbBlockReference::cast(entity);
        if (!reference || (!expectedOwner.isNull() && reference->ownerId() != expectedOwner))
            throw std::runtime_error("invalid block instance chain");
        ids.append(reference->objectId());
        expectedOwner = reference->blockTableRecord();
        result.transform = result.transform * reference->blockTransform();
    }
    auto* entity = open(db, handle, result.opened);
    if (!expectedOwner.isNull() && entity->ownerId() != expectedOwner)
        throw std::runtime_error("entity does not belong to the requested block definition");
    prepareResolved(entity, ids, result.transform, result);
    result.setupMs = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count();
}

AcDbRegion* prepareLocalArea(AcDbEntity* entity, Prepared& result) {
    if (!entity) throw std::runtime_error("null resolved entity");
    result.entityType = AcString(entity->isA()->name()).utf8Str();
    result.layer = AcString(entity->layer()).utf8Str();
    AcDbRegion* region = AcDbRegion::cast(entity);
    if (region) result.method = region->objectId().isNull()
        ? "transient_native_REGION" : "existing_REGION_full_subentity_path";
    else if (auto* hatch = AcDbHatch::cast(entity)) {
        result.area.reset(hatch->getRegionArea());
        region = result.area.get();
        result.method = "AcDbHatch.getRegionArea";
        if (!region) throw std::runtime_error("getRegionArea returned null");
    } else if (AcDbCurve::cast(entity)) {
        if (result.entityType != "AcDbPolyline" && result.entityType != "AcDbCircle"
            && result.entityType != "AcDbEllipse" && result.entityType != "AcDbSpline")
            throw std::runtime_error("curve has no supported native area query");
        AcArray<AcDbEntity*> input; input.append(entity);
        AcArray<AcDbRegion*> regions;
        const auto status = AcDbRegion::createFromCurves(input, regions);
        if (status == Acad::eOk && regions.length() == 1) result.area.reset(regions[0]);
        else {
            for (auto* value : regions) delete value;
            throw std::runtime_error("createFromCurves status " + std::to_string(int(status))
                + ", regions " + std::to_string(regions.length()));
        }
        region = result.area.get();
        result.method = "AcDbRegion.createFromCurves_read_only_no_closure";
    } else throw std::runtime_error("entity has no supported native area query");

    // The entity overload is documented for NON-database-active entities.
    // Preserve database identity even for the untransformed definition query.
    // Temporary native HATCH/curve regions have no ObjectId and use that overload.
    if (!region->objectId().isNull())
        require(result.local.set(AcDbFullSubentPath(entity->objectId(), kNullSubentId)), "local database BRep set");
    else require(result.local.set(*region), "local temporary BRep set");
    return region;
}

void prepareResolved(AcDbEntity* entity, const AcDbObjectIdArray& parents,
                     const AcGeMatrix3d& transform, Prepared& result) {
    const auto start = std::chrono::steady_clock::now();
    result.transform = transform;
    AcDbObjectIdArray ids = parents;
    auto* region = prepareLocalArea(entity, result);
    if (!region->objectId().isNull()) {
        ids.append(entity->objectId());
        require(result.world.set(AcDbFullSubentPath(ids, kNullSubentId)), "instance BRep set");
    } else if (parents.isEmpty()) require(result.world.set(*region), "temporary BRep set");
    else {
        // Only a detached native REGION, never a database-owned polyline clone.
        AcDbEntity* transformed = nullptr;
        const auto status = region->getTransformedCopy(result.transform, transformed);
        result.transformedArea.reset(transformed);
        if (status != Acad::eOk || !transformed)
            throw std::runtime_error("native REGION transform status " + std::to_string(int(status)));
        require(result.world.set(*transformed), "transformed BRep set");
    }
    AcBrBrepEdgeTraverser traversal;
    require(traversal.setBrep(result.world), "edge traversal start");
    unsigned count = 0;
    while (!traversal.done()) {
        if (++count > kEdgeBudget) throw std::runtime_error("bounded edge enumeration exceeded");
        AcBrEdge edge;
        require(traversal.getEdge(edge), "edge open");
        AcGeCurve3d* raw = nullptr;
        const auto status = edge.getCurve(raw);
        std::unique_ptr<AcGeCurve3d> curve(raw);
        require(status, "native world curve"); // Reject untransformed warning returns.
        if (!curve) throw std::runtime_error("null native edge curve");
        AcGeInterval interval; curve->getInterval(interval);
        if (!interval.isBounded() || !std::isfinite(interval.lowerBound())
            || !std::isfinite(interval.upperBound())) throw std::runtime_error("unbounded native edge");
        // Autodesk's exact gelib representation, NOT a sampled/repaired curve.
        // Unsupported or differently trimmed results retain the original API.
        std::unique_ptr<AcGeCurve3d> gelib;
        if (curve->isKindOf(AcGe::kExternalCurve3d)) {
            AcGeCurve3d* native = nullptr;
            if (static_cast<AcGeExternalCurve3d*>(curve.get())->isNativeCurve(native)) gelib.reset(native);
            else delete native;
            if (gelib) {
                AcGeInterval nativeInterval; gelib->getInterval(nativeInterval);
                const bool bounded = nativeInterval.isBounded()
                    && std::isfinite(nativeInterval.lowerBound()) && std::isfinite(nativeInterval.upperBound());
                if (!bounded || curve->evalPoint(interval.lowerBound()).distanceTo(
                        gelib->evalPoint(nativeInterval.lowerBound())) > kQueryTolerance
                    || curve->evalPoint(interval.upperBound()).distanceTo(
                        gelib->evalPoint(nativeInterval.upperBound())) > kQueryTolerance) gelib.reset();
            }
        }
        if (gelib) ++result.exactGelibCurves;
        result.gelibCurves.push_back(std::move(gelib));
        result.curves.push_back(std::move(curve));
        require(traversal.next(), "edge traversal next");
    }
    if (result.curves.empty()) throw std::runtime_error("empty native edge set");
    result.setupMs = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count();
}

Answer membership(const AcBrBrep& brep, const AcGePoint3d& point) {
    Answer result;
    AcGe::PointContainment containment = AcGe::kOutside;
    AcBrEntity* raw = nullptr;
    const auto status = brep.getPointContainment(point, containment, raw);
    std::unique_ptr<AcBrEntity> container(raw);
    result.status = int(status);
    if (raw) result.container = AcString(raw->isA()->name()).utf8Str();
    if (status == AcBr::eOk) {
        if (containment == AcGe::kOutside && !raw) result.membership = "outside";
        else if (containment == AcGe::kInside && raw) result.membership = "occupied";
        else if (containment == AcGe::kOnBoundary && result.container == "AcBrFace")
            result.membership = "occupied";
        else if (containment == AcGe::kOnBoundary
            && (result.container == "AcBrEdge" || result.container == "AcBrVertex"))
            result.membership = "edge";
    }
    return result;
}

Answer query(const Prepared& prepared, const AcGePoint3d& point, bool simplePoint, bool preferGelib) {
    const auto started = std::chrono::steady_clock::now();
    Answer answer = membership(prepared.world, point);
    const auto distanceStart = std::chrono::steady_clock::now();
    answer.containmentMs = std::chrono::duration<double, std::milli>(distanceStart - started).count();
    double best = std::numeric_limits<double>::infinity();
    AcGeTol tolerance; tolerance.setEqualPoint(kQueryTolerance); tolerance.setEqualVector(kQueryTolerance);
    for (std::size_t i = 0; i < prepared.curves.size(); ++i) {
        const auto& curve = preferGelib && prepared.gelibCurves[i]
            ? prepared.gelibCurves[i] : prepared.curves[i];
        AcGePoint3d candidate;
        if (simplePoint) {
            // Same bounded native curve and tolerance, alternative Autodesk API
            // returning XYZ instead of allocating a point-on-curve object.
            candidate = curve->closestPointTo(point, tolerance);
        } else {
            AcGePointOnCurve3d closest;
            curve->getClosestPointTo(point, closest, tolerance);
            candidate = closest.point();
            AcGeInterval interval; curve->getInterval(interval);
            if (!std::isfinite(closest.parameter()) || !interval.contains(closest.parameter()))
                return answer;
        }
        if (!finite(candidate)) return answer;
        const double distance = point.distanceTo(candidate);
        if (!std::isfinite(distance)) return answer;
        if (distance < best) { best = distance; answer.nearest = candidate; }
    }
    answer.distance = best;
    answer.distanceComplete = std::isfinite(best);
    answer.distanceMs = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - distanceStart).count();
    return answer;
}

bool stable(const Answer& a, const Answer& b, double& largest) {
    if (a.distanceComplete && b.distanceComplete)
        largest = std::max(largest, std::abs(a.distance - b.distance));
    return a.status == b.status && a.membership == b.membership && a.container == b.container
        && a.distanceComplete == b.distanceComplete
        && (!a.distanceComplete || std::abs(a.distance - b.distance) <= kQueryTolerance);
}
} // namespace ga::direct
