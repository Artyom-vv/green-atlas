#pragma once
#include <cstddef>

namespace ga::bridge {

inline constexpr const char* kPluginVersion = "0.1.42";
inline constexpr const char* kMcpQueueVersion = "v033";
inline constexpr double kRequestedToleranceMetres = 0.0001;
// Discovery bound for *review proposals*, not an automatic repair tolerance.
// Larger gaps remain open source paths and can still be corrected in AutoCAD.
inline constexpr double kMaximumAreaProposalClosureGapMetres = 0.02;
inline constexpr int kMaximumSamplingDepth = 24;
inline constexpr std::size_t kMaximumSampledPointsPerLoop = 16384;
inline constexpr std::size_t kMaximumTopologyElementsPerEntity = 16384;

}  // namespace ga::bridge
