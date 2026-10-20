#include "contracts.h"
#include <algorithm>
#include <charconv>

namespace ga::desktop {
namespace {
bool hex(char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); }
bool safeId(std::string_view value) {
    return !value.empty() && value.size() <= 256 && std::all_of(value.begin(), value.end(), [](char c) {
        return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
            || (c >= '0' && c <= '9') || c == '_' || c == '-';
    });
}
bool projectPrefix(std::string_view route, std::string_view prefix) {
    return route.size() > prefix.size() + 36 && route.substr(0, prefix.size()) == prefix
        && canonicalUuid(route.substr(prefix.size(), 36)) && route[prefix.size() + 36] == '/';
}
}
bool canonicalUuid(std::string_view value) {
    if (value.size() != 36) return false;
    for (size_t i = 0; i < value.size(); ++i)
        if (i == 8 || i == 13 || i == 18 || i == 23) { if (value[i] != '-') return false; }
        else if (!hex(value[i])) return false;
    return true;
}
bool ticketDirectoryId(std::string_view value) {
    return value.size() == 32 && std::all_of(value.begin(), value.end(), hex);
}
bool handoffRoute(std::string_view entry, std::string_view route) {
    if (!projectPrefix(route, "/projects/")) return false;
    const auto suffix = route.substr(47);
    return entry == "Drawing.autocad.json"
        ? suffix == "setup" || suffix == "setup?open=editor"
        : suffix == "import?source=cad";
}
bool projectRoute(std::string_view route) {
    if (route == "/projects") return true;
    if (!projectPrefix(route, "/projects/")) return false;
    const auto suffix = route.substr(47);
    return suffix == "setup" || suffix == "setup?open=editor"
        || suffix == "workspace" || suffix == "import?source=cad";
}
bool releaseArtifactRoute(std::string_view route) {
    if (!projectPrefix(route, "/api/projects/")) return false;
    auto rest = route.substr(51);
    constexpr std::string_view release = "releases/", artifact = "/artifacts/";
    if (rest.substr(0, release.size()) != release) return false;
    rest.remove_prefix(release.size());
    const auto split = rest.find(artifact);
    return split != std::string_view::npos && safeId(rest.substr(0, split))
        && safeId(rest.substr(split + artifact.size()));
}
bool renderImageRoute(std::string_view route) {
    if (!projectPrefix(route, "/api/projects/")) return false;
    auto rest = route.substr(51);
    constexpr std::string_view prefix = "scene-renders/";
    if (rest.substr(0, prefix.size()) != prefix) return false;
    rest.remove_prefix(prefix.size());
    return rest.size() == 42 && canonicalUuid(rest.substr(0, 36))
        && rest.substr(36) == "/image";
}
std::optional<uint16_t> loopbackPort(std::string_view origin) {
    constexpr std::string_view prefix = "http://127.0.0.1:";
    if (origin.substr(0, prefix.size()) != prefix) return std::nullopt;
    const auto number = origin.substr(prefix.size());
    if (number.empty() || number.size() > 5 || number.front() == '0') return std::nullopt;
    unsigned port = 0;
    const auto parsed = std::from_chars(number.data(), number.data() + number.size(), port);
    if (parsed.ec != std::errc{} || parsed.ptr != number.data() + number.size()
        || port < 1024 || port > 65535) return std::nullopt;
    return static_cast<uint16_t>(port);
}
bool runtimeSessionReady(std::string_view previousOrigin, std::string_view previousToken,
    std::string_view origin, std::string_view token) {
    if (!loopbackPort(origin) || (!previousOrigin.empty() && origin != previousOrigin)
        || token.size() < 43 || token.size() > 128) return false;
    if (!std::all_of(token.begin(), token.end(), [](char c) {
        return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
            || (c >= '0' && c <= '9') || c == '_' || c == '-';
    })) return false;
    return previousToken.empty() || previousToken != token;
}
const char* actionName(Action action) {
    switch (action) {
    case Action::library: return "library";
    case Action::handoff: return "handoff";
    case Action::close: return "close";
    case Action::quit: return "quit";
    }
    return "close";
}
void LeaveCoordinator::newDocument() { ++generation_; ready_ = false; request_.reset(); }
std::optional<LeaveRequest> LeaveCoordinator::begin(Action action, std::string target) {
    if (!ready_ || request_) return std::nullopt;
    if ((action == Action::library || action == Action::handoff) && !projectRoute(target)) return std::nullopt;
    request_ = LeaveRequest{generation_, ++sequence_, action, std::move(target)};
    return request_;
}
std::optional<LeaveRequest> LeaveCoordinator::decide(uint64_t generation, uint64_t id, bool allow) {
    if (!request_ || generation != generation_ || id != request_->id) return std::nullopt;
    auto accepted = request_;
    request_.reset();
    return allow ? accepted : std::nullopt;
}
}
