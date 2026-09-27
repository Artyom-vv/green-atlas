#pragma once
#include "direct_query_kernel.h"
#include <functional>

namespace ga::nativeQuery {
enum class QueryCapability { Area, Curve, ClosedArea };
struct ObjectTarget {
    std::string route;
    QueryCapability capability = QueryCapability::Area;
    std::vector<std::string> additionalRoutes;
    unsigned faceId = 0;
};
struct ObjectMeasurements {
    std::string route, entityType, layer, capability = "unavailable";
    std::string preparationError;
    std::vector<std::string> additionalRoutes;
    bool interiorKnown = false;
    unsigned faceId = 0;
    double prepareMs = 0;
    std::vector<ga::direct::Answer> answers;
    std::vector<std::string> queryErrors;
};
struct BatchMeasurements {
    std::vector<ObjectMeasurements> objects;
    double elapsedMs = 0;
};
inline constexpr std::size_t kBatchObjectLimit = 4096;
inline constexpr std::size_t kBatchPointLimit = 4096;
inline constexpr std::size_t kBatchMeasurementLimit = 262144;

// Measurements of explicitly identified instances, NOT a safety verdict or an
// assertion that every obstacle in a window has been covered. Caller owns that
// inventory/capture contract. Object failures retain their address and unknown.
BatchMeasurements measureBatch(AcDbDatabase& database,
    const std::vector<ObjectTarget>& targets, const std::vector<AcGePoint3d>& points,
    const std::function<bool()>& cancelled = {});
}
