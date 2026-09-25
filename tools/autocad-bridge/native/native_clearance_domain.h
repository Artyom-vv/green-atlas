#pragma once
#include "native_clearance_cache.h"
#include <array>
#include <optional>

namespace ga::clearance {
enum class Participation {Obstacle, Review, Site};
struct Constraint {
    ga::nativeQuery::ObjectTarget target;
    Participation participation = Participation::Obstacle;
    double distance = 0, reviewDistance = 0;
    std::optional<std::array<double,4>> bounds;
    std::string sourceError;
};
struct Issue {std::string route,reason;unsigned faceId=0;bool localized=false;};
struct Progress {std::size_t processed=0,cacheHits=0;double elapsedSeconds=0;};
// A job is an immutable-basis sequence of bounded batches. Copies own detached
// REGIONs; the transport can commit a batch only after capture checks succeed.
class Domain {
    Region possible,available,sites;
    bool siteSeen=false;
public:
    Progress progress;
    std::vector<Issue> issues;
    bool complete=false;
    double workArea=0;
    explicit Domain(const AcDbRegion& work);
    Domain(const Domain& other);
    void advance(AcDbDatabase& db,Cache& cache,const std::vector<Constraint>& batch,
                 bool final,const Cancel& cancelled = {});
    double availableArea() const;
    double excludedArea() const;
    double unresolvedArea() const;
    const AcDbRegion* availableRegion() const {return complete?available.get():nullptr;}
    Region unresolvedRegion() const;
};
}
