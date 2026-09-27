#pragma once
#include "native_clearance.h"
#include "native_query_batch.h"
#include <list>
#include <map>

namespace ga::clearance {
// Owned by ONE checked capture. Only detached native objects are retained;
// no AcDb object stays open between requests. clear() on close/source change.
// Distances and target identities are part of the key, never implicit globals.
struct Entry {
    Mask mask;
    std::string error;
    bool hit = false;
};
inline constexpr std::size_t kCachedMaskLimit = 8192;
inline constexpr std::size_t kCachedBoundaryPieceLimit = 131072;
class Cache {
    struct Stored {Entry value;std::list<std::string>::iterator recency;};
    std::map<std::string,Stored> entries;
    std::list<std::string> recent;
    std::size_t pieces = 0;
public:
    void clear();
    // Caller guarantees capture-current checks before AND after the operation.
    // Factory failures are addressable cached failures. Cancellation propagates
    // and does not poison a later retry.
    const Entry& get(const ga::nativeQuery::ObjectTarget& target,double distance,
                    const std::function<Mask()>& factory,const Cancel& cancelled = {});
    std::size_t size() const {return entries.size();}
};
}
