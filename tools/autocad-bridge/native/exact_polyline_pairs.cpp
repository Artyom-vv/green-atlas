#include "exact_polyline_pairs.h"
#include "drawing_traversal.h"
#include "surface_extraction.h"
#include "bridge_config.h"
#include "operation_control.h"
#include "cad_utils.h"
#include "dbcurve.h"
#include "dbpl.h"
#include "dbregion.h"
#include "dbidmap.h"
#include "dbsymtb.h"
#include "dbapserv.h"
#include "gemat3d.h"
#include "gegbl.h"
#include "getol.h"
#include <algorithm>
#include <cmath>
#include <memory>
#include <set>
#include <tuple>

namespace ga::bridge {
namespace {
NativeEndpoint endpointKey(const AcGePoint3d& point) {
    return {point.x, point.y, point.z};
}

AcGePoint3d nativePoint(const NativeEndpoint& value) {
    return {std::get<0>(value), std::get<1>(value), std::get<2>(value)};
}

// Endpoint identity uses Autodesk's numerical equality, NOT the much larger
// display/sampling tolerance. DXFOUT can round equivalent doubles by ~2e-12.
// No vertex is moved: original read-open curves still go to createFromCurves.
// Each endpoint must have exactly one neighbour among ALL same-layer curves;
// branches and chains of almost-equal points remain ineligible.
std::vector<int> uniqueEndpointPartners(const std::vector<NativeCurveCandidate>& curves) {
    struct Endpoint { std::string layer; NativeEndpoint point; std::size_t index; };
    std::vector<Endpoint> endpoints;
    for (std::size_t i = 0; i < curves.size(); ++i) {
        endpoints.push_back({curves[i].sourceLayer, curves[i].first, 2 * i});
        endpoints.push_back({curves[i].sourceLayer, curves[i].last, 2 * i + 1});
    }
    std::sort(endpoints.begin(), endpoints.end(), [](const auto& a, const auto& b) {
        return std::tie(a.layer, a.point, a.index) < std::tie(b.layer, b.point, b.index);
    });
    std::vector<int> partners(endpoints.size(), -1);
    const double epsilon = AcGeContext::gTol.equalPoint();
    for (std::size_t i = 0; i < endpoints.size(); ++i) {
        if (operationCancelled()) return {};
        const auto& a = endpoints[i];
        for (std::size_t j = i + 1; j < endpoints.size(); ++j) {
            const auto& b = endpoints[j];
            if (a.layer != b.layer || std::get<0>(b.point) - std::get<0>(a.point) > epsilon) break;
            if (!nativePoint(a.point).isEqualTo(nativePoint(b.point))) continue;
            partners[a.index] = partners[a.index] == -1 ? static_cast<int>(b.index) : -2;
            partners[b.index] = partners[b.index] == -1 ? static_cast<int>(a.index) : -2;
        }
    }
    return partners;
}
}  // namespace

#ifdef GA_GEOMETRY_SELFTEST
bool nativeEndpointPartnersSelftest() {
    NativeCurveCandidate first, second, branch;
    first.sourceLayer = second.sourceLayer = branch.sourceLayer = "fixture";
    first.first = {16000.0, 0.0, 0.0}; first.last = {16010.0, 0.0, 0.0};
    second.first = first.last;
    second.last = {std::nextafter(16000.0, 17000.0), 0.0, 0.0};
    if (uniqueEndpointPartners({first, second}) != std::vector<int>{3, 2, 1, 0}) return false;
    branch.first = first.first; branch.last = {16000.0, 5.0, 0.0};
    auto branched = uniqueEndpointPartners({first, second, branch});
    if (branched[0] != -2 || branched[3] != -2 || branched[4] != -2) return false;
    second.last = {16000.0145, 0.0, 0.0};
    auto gap = uniqueEndpointPartners({first, second});
    if (gap[0] != -1 || gap[3] != -1) return false;
    second.last = first.first;
    second.sourceLayer = "other";
    return uniqueEndpointPartners({first, second}) == std::vector<int>{-1, -1, -1, -1};
}
#endif

bool inspectNativeCurveCandidate(AcDbEntity* entity, NativeCurveCandidate& output) {
    AcDbCurve* curve = AcDbCurve::cast(entity);
    if (!curve || curve->isClosed()) return false;
    AcGePoint3d first, last;
    if (curve->getStartPoint(first) != Acad::eOk ||
        curve->getEndPoint(last) != Acad::eOk ||
        !std::isfinite(first.x) || !std::isfinite(first.y) || !std::isfinite(first.z) ||
        !std::isfinite(last.x) || !std::isfinite(last.y) || !std::isfinite(last.z) ||
        endpointKey(first) == endpointKey(last)) return false;
    output = {entity->objectId(), entityHandle(entity), utf8(entity->layer()),
              endpointKey(first), endpointKey(last),
              AcDbPolyline::cast(entity) != nullptr, false};
    return true;
}

void collectExactPolylinePairs(const std::vector<NativeCurveCandidate>& curves,
                               const AcGeMatrix3d& transform,
                               const std::vector<std::string>& instanceChain,
                               const std::string& inheritedLayer,
                               const std::string& layerNamespace,
                               double tolerance,
                               std::vector<RegionTopology>& regions) {
    const auto partners = uniqueEndpointPartners(curves);
    if (partners.size() != 2 * curves.size()) return;
    for (std::size_t index = 0; index < curves.size(); ++index) {
        if (operationCancelled()) return;
        const int a = partners[2 * index], b = partners[2 * index + 1];
        if (a < 0 || b < 0 || a / 2 != b / 2 || a == b ||
            static_cast<std::size_t>(a / 2) <= index ||
            partners[a] != static_cast<int>(2 * index) ||
            partners[b] != static_cast<int>(2 * index + 1)) continue;
        const auto& left = curves[index];
        const auto& right = curves[a / 2];
        if (!left.polyline || !left.pathResolved || !right.polyline || !right.pathResolved) continue;
        AcDbPolyline* first = nullptr;
        AcDbPolyline* second = nullptr;
        const auto firstStatus = acdbOpenObject(first, left.id, AcDb::kForRead);
        const auto secondStatus = acdbOpenObject(second, right.id, AcDb::kForRead);
        if (firstStatus != Acad::eOk || secondStatus != Acad::eOk ||
            !first || !second) {
            if (first) first->close();
            if (second) second->close();
            continue;
        }
        AcArray<AcDbEntity*> curves;
        curves.append(first);
        curves.append(second);
        AcArray<AcDbRegion*> created;
        const auto status = AcDbRegion::createFromCurves(curves, created);
        if (status == Acad::eOk && created.length() == 1 && created[0]) {
            RegionTopology topology;
            topology.sourceLayer = left.sourceLayer;
            topology.measurement.layer = effectiveEntityLayer(
                left.sourceLayer, inheritedLayer, layerNamespace);
            topology.instanceChain = instanceChain;
            topology.sourceHandles = {left.handle, right.handle};
            std::sort(topology.sourceHandles.begin(), topology.sourceHandles.end());
            topology.measurement.handle = topology.sourceHandles.front();
            if (extractRegionTopology(created[0], transform, tolerance, topology) &&
                topology.measurement.area > 0.0) {
                regions.push_back(std::move(topology));
            }
        }
        // Autodesk can return partial objects even on an error status.
        for (int index = 0; index < created.length(); ++index) delete created[index];
        first->close();
        second->close();
    }
}

void collectNearClosedAreaProposals(
    const std::vector<NativeCurveCandidate>& curves,
    const AcGeMatrix3d& transform,
    const std::vector<std::string>& instanceChain,
    const std::string& inheritedLayer,
    const std::string& layerNamespace,
    double tolerance,
    const std::vector<RegionTopology>& activeRegions,
    std::vector<NativeAreaProposal>& proposals,
    RegionTraversalDiagnostics& diagnostics) {
    std::set<std::string> alreadyDerived;
    for (const auto& region : activeRegions) {
        if (region.instanceChain != instanceChain) continue;
        alreadyDerived.insert(region.sourceHandles.begin(), region.sourceHandles.end());
    }
    // No user document or source XREF is opened for write. The scratch DB is
    // AutoCAD-owned but entirely transient; created regions never become DWG
    // entities. Autodesk requires curve inputs read-open from a database.
    std::unique_ptr<AcDbDatabase> scratch;
    const double maximumGapUnits =
        tolerance * kMaximumAreaProposalClosureGapMetres / kRequestedToleranceMetres;
    for (const auto& curve : curves) {
        if (operationCancelled()) return;
        if (!curve.polyline || !curve.pathResolved ||
            alreadyDerived.count(curve.handle)) continue;
        AcGePoint3d first(std::get<0>(curve.first), std::get<1>(curve.first),
                          std::get<2>(curve.first));
        AcGePoint3d last(std::get<0>(curve.last), std::get<1>(curve.last),
                         std::get<2>(curve.last));
        first.transformBy(transform);
        last.transformBy(transform);
        const double gap = std::hypot(first.x - last.x, first.y - last.y);
        if (!std::isfinite(gap) || gap <= 0.0 || gap > maximumGapUnits) continue;
        ++diagnostics.areaProposalCandidates;
        const auto reject = [&](const std::string& stage, const int nativeStatus = 0) {
            ++diagnostics.areaProposalRejected;
            diagnostics.areaProposalRejections.push_back(
                {curve.handle, instanceChain,
                 effectiveEntityLayer(curve.sourceLayer, inheritedLayer,
                                      layerNamespace),
                 gap, stage, nativeStatus});
        };

        AcDbPolyline* source = nullptr;
        if (acdbOpenObject(source, curve.id, AcDb::kForRead) != Acad::eOk ||
            source == nullptr) {
            reject("source-open");
            continue;
        }
        if (source->numVerts() < 4) {
            reject("source-too-short");
            source->close();
            continue;
        }
        source->close();
        if (!scratch) scratch = std::make_unique<AcDbDatabase>(true, true);
        AcDbBlockTable* table = nullptr;
        Acad::ErrorStatus status = scratch->getBlockTable(table, AcDb::kForRead);
        AcDbObjectId modelId;
        if (status == Acad::eOk && table) {
            status = table->getAt(ACDB_MODEL_SPACE, modelId);
            table->close();
        }
        if (status != Acad::eOk || modelId.isNull() ||
            curve.id.database() == nullptr) {
            reject("scratch-model", static_cast<int>(status));
            continue;
        }
        AcDbObjectIdArray originals;
        originals.append(curve.id);
        AcDbIdMapping idMap;
        status = curve.id.database()->wblockCloneObjects(
            originals, modelId, idMap, AcDb::kDrcIgnore);
        AcDbObjectId cloneId;
        AcDbIdPair pair;
        pair.setKey(curve.id);
        if (status == Acad::eOk && idMap.compute(pair)) cloneId = pair.value();
        if (status != Acad::eOk || cloneId.isNull()) {
            reject("scratch-wblock-clone", static_cast<int>(status));
            continue;
        }
        AcDbPolyline* writeClone = nullptr;
        status = acdbOpenObject(writeClone, cloneId, AcDb::kForWrite);
        if (status != Acad::eOk || !writeClone) {
            reject("scratch-clone-write-open", static_cast<int>(status));
            continue;
        }
        writeClone->setClosed(Adesk::kTrue);
        const bool cloneClosed = writeClone->isClosed();
        writeClone->close();
        if (!cloneClosed) {
            reject("clone-not-closed");
            continue;
        }
        AcDbPolyline* readClone = nullptr;
        status = acdbOpenObject(readClone, cloneId, AcDb::kForRead);
        AcArray<AcDbRegion*> created;
        if (status == Acad::eOk && readClone &&
            readClone->isReadEnabled() && !readClone->isWriteEnabled()) {
            AcArray<AcDbEntity*> input;
            input.append(readClone);
            status = AcDbRegion::createFromCurves(input, created);
        }
        if (readClone) readClone->close();
        if (status == Acad::eOk && created.length() == 1 && created[0]) {
            NativeAreaProposal proposal;
            auto& preview = proposal.preview;
            preview.sourceLayer = curve.sourceLayer;
            preview.measurement.layer = effectiveEntityLayer(
                curve.sourceLayer, inheritedLayer, layerNamespace);
            preview.measurement.handle = curve.handle;
            preview.instanceChain = instanceChain;
            proposal.closureGapWcsXyUnits = gap;
            if (extractRegionTopology(created[0], transform, tolerance, preview) &&
                preview.resolved && preview.measurement.area > 0.0) {
                proposals.push_back(std::move(proposal));
            } else {
                reject("native-topology", preview.errorStatus);
            }
        } else {
            reject("create-from-curves", static_cast<int>(status));
        }
        // Autodesk may return partial regions on failure; all are transient.
        for (int index = 0; index < created.length(); ++index) delete created[index];
        AcDbPolyline* eraseClone = nullptr;
        if (acdbOpenObject(eraseClone, cloneId, AcDb::kForWrite) == Acad::eOk &&
            eraseClone) {
            eraseClone->erase();
            eraseClone->close();
        }
    }
}

}  // namespace ga::bridge
