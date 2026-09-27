#pragma once
#include "native_affine_query.h"

namespace ga::nativeQuery {
// Endpoint equality in drawing units. This is not permission to bridge gaps.
inline constexpr double kNativeJoinTolerance = 1e-8;
// One exact connected cycle of authored curves in a common definition frame.
// No closure chords, endpoint movement, layer inference, or partial acceptance.
// Caller retains all read-open input entities for the lifetime of this query.
class AreaGroupQuery {
    struct Impl;
    std::unique_ptr<Impl> impl;
public:
    AreaGroupQuery();
    ~AreaGroupQuery();
    AreaGroupQuery(const AreaGroupQuery&) = delete;
    AreaGroupQuery& operator=(const AreaGroupQuery&) = delete;
    void prepare(const std::vector<AcDbEntity*>& curves, const AcGeMatrix3d& transform);
    ga::direct::Answer queryPlanar(const AcGePoint3d& point) const;
    const AffineEvidence& evidence() const;
    // Exact detached REGION for downstream AutoCAD boolean/offset operations.
    std::unique_ptr<AcDbRegion> copyWorldRegion() const;
};
inline constexpr std::size_t kAreaGroupMemberLimit = 256;
}
