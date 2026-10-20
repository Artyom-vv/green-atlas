#pragma once
#include "contracts.h"
#include "../vendor/nlohmann/json.hpp"
#include <optional>
#include <string>
#include <string_view>

namespace ga::desktop::windows {
using Json = nlohmann::json;
bool launchMetadata(const Json& value);
bool sameOrigin(std::string_view origin, std::string_view url);
bool externalHttpUrl(std::string_view url);
bool supportedWebViewVersion(std::string_view value);
bool handoffId(std::string_view value);
bool sourceDxfRoute(std::string_view route);
bool downloadRoute(std::string_view route);
bool downloadType(std::string_view route, std::string_view mime, std::string_view filename);
bool privateApiRoute(std::string_view method, std::string_view route);
bool safeInteger(const Json& value, bool zero = false);
// Документ владеет draft/write policy. Host проверяет только корреляцию и источник.
class Bridge {
public:
    LeaveCoordinator leave;
    std::string origin, token;
    bool runtime = false;
    bool documentFailed = false;
    bool acceptReady(const Json& value);
    void newDocument();
    void failedDocument();
    void unavailable();
    std::optional<Json> receive(const Json& value, std::string_view source);
    Json event(std::string_view type) const;
    Json navigation(const LeaveRequest& request) const;
};
bool dependencyPickerRequest(const Json& value, std::string_view source, const Bridge& bridge);
struct DownloadOffer {
    uint64_t generation;
    std::string origin, token, route, mime, filename;
    bool current(uint64_t activeGeneration, const Bridge& bridge, bool stopping) const;
};
Json buildInfo();
bool selfTest();
}
