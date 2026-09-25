#pragma once
// Shared AutoCAD membership/nearest-curve primitives. No sampled geometry.
#include "dbapserv.h"
#include "dbregion.h"
#include "brbrep.h"
#include "gecurv3d.h"
#include "gemat3d.h"
#include <memory>
#include <string>
#include <vector>

namespace ga::direct {
struct ReadEntities {
    std::vector<AcDbEntity*> values;
    ReadEntities() = default;
    ReadEntities(const ReadEntities&) = delete;
    ReadEntities& operator=(const ReadEntities&) = delete;
    ~ReadEntities() { for (auto* entity : values) entity->close(); }
};
struct Prepared {
    ReadEntities opened;
    std::unique_ptr<AcDbRegion> area;
    std::unique_ptr<AcDbEntity> transformedArea;
    AcGeMatrix3d transform;
    AcBrBrep local, world;
    std::vector<std::unique_ptr<AcGeCurve3d>> curves;
    std::vector<std::unique_ptr<AcGeCurve3d>> gelibCurves;
    unsigned exactGelibCurves = 0;
    std::string method, entityType, layer;
    double setupMs = 0;
};
struct Answer {
    int status = -1;
    std::string membership = "unknown", container;
    bool distanceComplete = false;
    double distance = 0;
    double containmentMs = 0, distanceMs = 0;
    AcGePoint3d nearest;
};
void prepare(AcDbDatabase& database, const std::string& handle,
             const std::vector<std::string>& chain, Prepared& output);
// Caller retains read-open entity/parents for the lifetime of Prepared.
// ObjectIds and transform come from traversal of the actual loaded database.
void prepareResolved(AcDbEntity* entity, const AcDbObjectIdArray& parents,
                     const AcGeMatrix3d& transform, Prepared& output);
// Native local topology only; also used by the bounded nonuniform API probe.
AcDbRegion* prepareLocalArea(AcDbEntity* entity, Prepared& output);
Answer membership(const AcBrBrep& brep, const AcGePoint3d& point);
Answer query(const Prepared& prepared, const AcGePoint3d& worldPoint,
             bool simplePoint = false, bool preferGelib = false);
bool stable(const Answer& a, const Answer& b, double& largestDistanceDelta);
} // namespace ga::direct
