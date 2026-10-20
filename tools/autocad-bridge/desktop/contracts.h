#pragma once
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace ga::desktop {
bool canonicalUuid(std::string_view value);
bool ticketDirectoryId(std::string_view value);
bool handoffRoute(std::string_view entry, std::string_view route);
bool projectRoute(std::string_view route);
bool releaseArtifactRoute(std::string_view route);
bool renderImageRoute(std::string_view route);
std::optional<uint16_t> loopbackPort(std::string_view origin);
bool runtimeSessionReady(std::string_view previousOrigin, std::string_view previousToken,
    std::string_view origin, std::string_view token);
enum class Action { library, handoff, close, quit };
const char* actionName(Action action);
struct LeaveRequest { uint64_t generation; uint64_t id; Action action; std::string target; };
// Correlate decisions with the current document. The web application alone
// decides whether its draft/write state permits leaving.
class LeaveCoordinator {
public:
    void newDocument();
    void ready() { ready_ = true; }
    bool isReady() const { return ready_; }
    uint64_t generation() const { return generation_; }
    bool pending() const { return request_.has_value(); }
    std::optional<LeaveRequest> begin(Action action, std::string target = {});
    std::optional<LeaveRequest> decide(uint64_t generation, uint64_t id, bool allow);
    void cancel() { request_.reset(); }
private:
    uint64_t generation_ = 0;
    uint64_t sequence_ = 0;
    bool ready_ = false;
    std::optional<LeaveRequest> request_;
};
}
