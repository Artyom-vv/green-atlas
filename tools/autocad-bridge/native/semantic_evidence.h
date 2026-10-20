#pragma once
#include "geometry_types.h"
#include <optional>
#include <string>

namespace ga::capture {
inline constexpr std::size_t kMaximumSemanticValueBytes = 4096;
inline constexpr std::size_t kMaximumSemanticRows = 100000;
inline constexpr std::size_t kMaximumSemanticValueBudget = 16 * 1024 * 1024;
inline constexpr std::size_t kMaximumSemanticPayloadBytes = 32 * 1024 * 1024;

// Local authored content only. An anchor does not associate a label with a pipe
// or surface; attribute definition defaults are never instance measurements.
struct SemanticValue {
    std::string origin, value, tag, status = "captured";
    std::optional<ga::bridge::Point3> anchorLocal, anchorWcs;
    bool invisible = false;
};

class SemanticCatalogue {
public:
    void consider(std::size_t index, const std::string& identityJson,
                  bool eligible, const SemanticValue* value);
    std::string receipt(const std::string& instancesSha256,
                        const std::string& displaySha256,
                        std::size_t instanceCount) const;
    std::size_t emitted() const { return emittedCount_; }
private:
    std::string rows_;
    std::size_t eligibleCount_ = 0, emittedCount_ = 0, omittedCount_ = 0;
    bool allCaptured_ = true;
};
}
