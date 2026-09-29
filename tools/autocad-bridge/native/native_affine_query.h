#pragma once
#include "direct_query_kernel.h"
#include "genurb3d.h"

namespace ga::nativeQuery {
// Evidence comes from native topology, not a sampled display polygon.
struct AffineEvidence {
    AcGePoint3d low,high;
    std::vector<AcGePoint3d> edgeWitnesses;
    std::vector<std::pair<AcGePoint3d,AcGePoint3d>> straightEdges;
    int regionAreaStatus=-1;
    double regionAreaValue=0;
    unsigned faces=0;
    bool allStraight=true;
    double maxFit=0,maxWitnessDistance=0,localArea=0,jacobian=0,worldZ=0;
    std::size_t edgeCount = 0;
};
// Retain the read-open source entity until this prepared query is destroyed.
// Membership is queried in source coordinates; distances use world native edges.
class AffineAreaQuery {
    struct Impl;
    std::unique_ptr<Impl> impl;
public:
    AffineAreaQuery();
    ~AffineAreaQuery();
    AffineAreaQuery(const AffineAreaQuery&) = delete;
    AffineAreaQuery& operator=(const AffineAreaQuery&) = delete;
    void prepare(AcDbEntity& entity, const AcGeMatrix3d& transform, bool deferDistances = false);
    ga::direct::Answer query(const AcGePoint3d& world) const;
    ga::direct::Answer queryPlanar(const AcGePoint3d& point) const;
    // Exact native containment only. Does not report or approximate distances.
    ga::direct::Answer membershipPlanar(const AcGePoint3d& point) const;
    const AffineEvidence& evidence() const;
    double analyticDistance(const AcGePoint3d& point) const;
};
}
