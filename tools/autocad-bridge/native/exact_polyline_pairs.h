#pragma once
#include "geometry_types.h"
#include "dbid.h"
#include <tuple>
class AcGeMatrix3d;
class AcDbEntity;

namespace ga::bridge {

using NativeEndpoint = std::tuple<double, double, double>;

struct NativeCurveCandidate {
    AcDbObjectId id;
    std::string handle;
    std::string sourceLayer;
    NativeEndpoint first;
    NativeEndpoint last;
    bool polyline = false;
    bool pathResolved = false;
};

// Inspect while the main traversal already holds the entity open for read.
// This avoids reopening every curve in a large municipal drawing.
bool inspectNativeCurveCandidate(AcDbEntity* entity, NativeCurveCandidate& output);

#ifdef GA_GEOMETRY_SELFTEST
bool nativeEndpointPartnersSelftest();
#endif

// Read-only ObjectARX surface extraction from unambiguous authored pairs.
void collectExactPolylinePairs(const std::vector<NativeCurveCandidate>& curves,
                               const AcGeMatrix3d& transform,
                               const std::vector<std::string>& instanceChain,
                               const std::string& inheritedLayer,
                               const std::string& layerNamespace,
                               double tolerance,
                               std::vector<RegionTopology>& regions);

// Advisory only: native REGION extraction on temporary, non-document clones.
// The source path remains open and independently visible in the snapshot.
void collectNearClosedAreaProposals(
    const std::vector<NativeCurveCandidate>& curves,
    const AcGeMatrix3d& transform,
    const std::vector<std::string>& instanceChain,
    const std::string& inheritedLayer,
    const std::string& layerNamespace,
    double tolerance,
    const std::vector<RegionTopology>& activeRegions,
    std::vector<NativeAreaProposal>& proposals,
    RegionTraversalDiagnostics& diagnostics);

}  // namespace ga::bridge
