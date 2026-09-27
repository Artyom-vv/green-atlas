#pragma once
#include "dbents.h"
#include <array>
#include <map>
#include <string>
#include <vector>

namespace ga::nativeQuery {
struct AreaCandidate {
    std::vector<std::string> routes;
    std::string layer;
    std::string error;
};

// Collect BEFORE window culling, while each authored entity is read-open.
// Same-instance/layer connected cycles are candidates, NOT accepted areas.
// AreaGroupQuery must still establish one native face and full perimeter.
// No vertices are changed and no missing edge is supplied by this collector.
class AreaCandidates {
    struct Curve {
        std::string route;
        std::array<AcGePoint3d, 2> ends;
    };
    using Scope = std::pair<std::string, std::string>; // instance prefix + native layer
    std::map<Scope, std::vector<Curve>> scopes;
    std::vector<AreaCandidate> unavailable;
public:
    unsigned observed = 0, individuallyClosed = 0;
    void consider(AcDbEntity& entity, const std::string& route);
    std::vector<AreaCandidate> collect() const;
};
}
