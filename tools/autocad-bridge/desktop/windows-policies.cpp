#include "windows-policies.h"
#include <algorithm>
#include <array>
#include <charconv>
#include <cctype>
#include <set>

#ifndef GREEN_ATLAS_DESKTOP_VERSION
#define GREEN_ATLAS_DESKTOP_VERSION "candidate"
#endif

namespace ga::desktop::windows {
bool launchMetadata(const Json& value) {
    return value == Json{{"schema", "green-atlas.desktop-launch/1"},
        {"kind", "windows_webview2"}, {"host", "GreenAtlasDesktop.exe"},
        {"runtime", "Runtime/GreenAtlasRuntime.exe"}, {"web", "Web"}, {"worker", "Worker"}};
}
bool sameOrigin(std::string_view origin, std::string_view url) {
    return loopbackPort(origin) && (url == origin ||
        (url.size() > origin.size() && url.substr(0, origin.size()) == origin
            && url[origin.size()] == '/'));
}
bool externalHttpUrl(std::string_view url) {
    if (url.empty() || url.size() > 8192 || std::any_of(url.begin(), url.end(),
        [](unsigned char c) { return c <= 32 || c == 127 || c == '\\'; })) return false;
    const auto prefix = url.substr(0, 8) == "https://" ? 8u : url.substr(0, 7) == "http://" ? 7u : 0u;
    if (!prefix) return false;
    const auto authority = url.substr(prefix, url.find_first_of("/?#", prefix) - prefix);
    return !authority.empty() && authority.find_first_of("@%") == std::string_view::npos;
}
bool supportedWebViewVersion(std::string_view value) {
    std::array<unsigned, 4> version{};
    for (size_t index = 0; index < version.size(); ++index) {
        const auto end = value.find('.');
        const auto part = value.substr(0, end);
        if (part.empty()) return false;
        const auto parsed = std::from_chars(part.data(), part.data() + part.size(), version[index]);
        if (parsed.ec != std::errc{} || parsed.ptr != part.data() + part.size()) return false;
        if (index + 1 == version.size()) { if (end != std::string_view::npos) return false; }
        else { if (end == std::string_view::npos) return false; value.remove_prefix(end + 1); }
    }
    return version >= std::array<unsigned, 4>{152, 0, 4191, 53};
}
bool handoffId(std::string_view value) {
    return value.size() == 64 && std::all_of(value.begin(), value.end(), [](char c) {
        return (c >= 'a' && c <= 'f') || (c >= '0' && c <= '9');
    });
}
bool sourceDxfRoute(std::string_view route) {
    constexpr std::string_view prefix = "/api/projects/", suffix = "/source-dxf/download";
    if (route.substr(0, prefix.size()) != prefix || !canonicalUuid(route.substr(prefix.size(), 36))) return false;
    auto rest = route.substr(prefix.size() + 36);
    if (rest.substr(0, suffix.size()) != suffix) return false;
    rest.remove_prefix(suffix.size());
    constexpr std::string_view query = "?expected_source_sha256=";
    return rest.empty() || (rest.substr(0, query.size()) == query && handoffId(rest.substr(query.size())));
}
bool downloadRoute(std::string_view route) {
    return releaseArtifactRoute(route) || renderImageRoute(route) || sourceDxfRoute(route);
}
bool downloadType(std::string_view route, std::string_view mime, std::string_view filename) {
    if (!downloadRoute(route) || filename.empty() || filename.size() > 1024
        || filename == "." || filename == ".." || filename.back() == '.' || filename.back() == ' '
        || filename.find_first_of("/\\:\r\n\t<>\"|?*") != std::string_view::npos
        || std::any_of(filename.begin(), filename.end(), [](unsigned char c) { return c < 32; })) return false;
    const auto dot = filename.find_last_of('.');
    if (dot == std::string_view::npos) return false;
    std::string base(filename.substr(0, filename.find('.')));
    std::transform(base.begin(), base.end(), base.begin(),
        [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
    if (base == "CON" || base == "PRN" || base == "AUX" || base == "NUL"
        || (base.size() == 4 && (base.substr(0, 3) == "COM" || base.substr(0, 3) == "LPT")
            && base[3] >= '1' && base[3] <= '9')) return false;
    std::string extension(filename.substr(dot));
    std::transform(extension.begin(), extension.end(), extension.begin(),
        [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    mime = mime.substr(0, mime.find(';'));
    if (renderImageRoute(route)) return mime == "image/png" && extension == ".png";
    if (sourceDxfRoute(route)) return mime == "application/dxf" && extension == ".dxf";
    // Сохраняются текущие отдельные артефакты выпуска, включая таблицу и SVG.
    return (mime == "application/zip" && extension == ".zip")
        || (mime == "text/csv" && extension == ".csv")
        || (mime == "application/json" && extension == ".json")
        || (mime == "image/svg+xml" && extension == ".svg")
        || (mime == "application/dxf" && extension == ".dxf");
}
bool privateApiRoute(std::string_view method, std::string_view route) {
    constexpr std::string_view base = "/_desktop/handoffs";
    if (route == "/_desktop/shutdown") return method == "POST";
    if (route == base) return method == "GET" || method == "POST";
    if (route.size() < base.size() + 65 || route.substr(0, base.size() + 1) != std::string(base) + "/") return false;
    const auto rest = route.substr(base.size() + 1);
    if (!handoffId(rest.substr(0, 64))) return false;
    const auto suffix = rest.substr(64);
    return (method == "GET" && suffix.empty())
        || (method == "POST" && (suffix == "/cancel" || suffix == "/resume"));
}
bool safeInteger(const Json& value, bool zero) {
    if (!value.is_number_unsigned() && !value.is_number_integer()) return false;
    if (value.is_number_integer() && value.get<int64_t>() < (zero ? 0 : 1)) return false;
    const auto number = value.get<uint64_t>();
    return number >= (zero ? 0u : 1u) && number <= 9007199254740991ULL;
}
bool Bridge::acceptReady(const Json& value) try {
    if (!value.is_object() || value.size() != 4 || value.value("service", "") != "green-atlas-desktop"
        || !value.contains("protocol") || !safeInteger(value["protocol"]) || value["protocol"] != 1
        || !value.contains("origin") || !value["origin"].is_string()
        || !value.contains("session_token") || !value["session_token"].is_string()) return false;
    const auto nextOrigin = value["origin"].get<std::string>();
    const auto nextToken = value["session_token"].get<std::string>();
    if (!runtimeSessionReady(origin, token, nextOrigin, nextToken)) return false;
    origin = nextOrigin; token = nextToken; runtime = true; return true;
} catch (const Json::exception&) { return false; }
void Bridge::newDocument() { documentFailed = false; leave.newDocument(); }
void Bridge::failedDocument() { documentFailed = true; leave.cancel(); }
void Bridge::unavailable() { runtime = false; leave.cancel(); }
Json Bridge::event(std::string_view type) const {
    return {{"schemaVersion", 1}, {"type", type}, {"origin", origin},
        {"documentGeneration", leave.generation()}};
}
Json Bridge::navigation(const LeaveRequest& request) const {
    auto value = event("navigationRequest");
    value["requestId"] = request.id;
    value["action"] = actionName(request.action);
    if (!request.target.empty()) value["target"] = request.target;
    return value;
}
bool dependencyPickerRequest(const Json& value, std::string_view source, const Bridge& bridge) try {
    if (!bridge.runtime || bridge.documentFailed || !bridge.leave.isReady()
        || !sameOrigin(bridge.origin, source) || !value.is_object() || value.size() != 6
        || value.value("origin", "") != bridge.origin || value.value("type", "") != "chooseDependency"
        || !value.contains("schemaVersion") || value["schemaVersion"] != 1
        || !value.contains("documentGeneration") || !safeInteger(value["documentGeneration"])
        || value["documentGeneration"] != bridge.leave.generation()
        || !value.contains("requestId") || !safeInteger(value["requestId"])) return false;
    const auto field = value.value("field", "");
    return field == "autocad_root" || field == "autocad_template" || field == "blender"
        || field == "botaniq_root" || field == "pbr_root";
} catch (const Json::exception&) { return false; }
std::optional<Json> Bridge::receive(const Json& value, std::string_view source) try {
    if (documentFailed || !sameOrigin(origin, source) || !value.is_object() || !value.contains("schemaVersion")
        || !safeInteger(value["schemaVersion"]) || value["schemaVersion"] != 1
        || value.value("origin", "") != origin) return std::nullopt;
    if (value.value("type", "") == "ready") {
        leave.cancel(); leave.ready(); return event("capabilities");
    }
    if (value.value("type", "") != "decision" || !value.contains("allow") || !value["allow"].is_boolean()
        || !value.contains("documentGeneration") || !safeInteger(value["documentGeneration"])
        || !value.contains("requestId") || !safeInteger(value["requestId"])) return std::nullopt;
    auto result = leave.decide(value["documentGeneration"].get<uint64_t>(),
        value["requestId"].get<uint64_t>(), value["allow"].get<bool>());
    if (!result) return std::nullopt;
    auto accepted = navigation(*result); accepted["type"] = "accepted"; return accepted;
} catch (const Json::exception&) { return std::nullopt; }
bool DownloadOffer::current(uint64_t activeGeneration, const Bridge& bridge, bool stopping) const {
    return !stopping && bridge.runtime && !bridge.documentFailed && generation == activeGeneration
        && origin == bridge.origin && token == bridge.token && downloadType(route, mime, filename);
}
Json buildInfo() {
    return {{"schema", "green-atlas.desktop-build/1"}, {"component", "windows_webview2_host"},
        {"version", GREEN_ATLAS_DESKTOP_VERSION}, {"desktop_protocol", 1},
        {"launch_schema", "green-atlas.desktop-launch/1"}, {"sdk", "1.0.4191.47"},
        {"minimum_webview2_runtime", "152.0.4191.53"},
        {"loader", "static"}, {"architecture", "x86_64"}, {"qualification", "not_claimed"}};
}
bool selfTest() {
    Bridge bridge;
    const std::string origin = "http://127.0.0.1:8123", a(43, 'a'), b(43, 'b');
    if (!bridge.acceptReady({{"service", "green-atlas-desktop"}, {"protocol", 1}, {"origin", origin}, {"session_token", a}})) return false;
    bridge.newDocument();
    if (bridge.leave.begin(Action::close)) return false;
    auto ready = bridge.receive({{"schemaVersion", 1}, {"type", "ready"}, {"origin", origin}}, origin + "/projects");
    if (!ready || !bridge.leave.isReady()) return false;
    auto request = bridge.leave.begin(Action::close);
    if (!request) return false;
    auto decision = Json{{"schemaVersion", 1}, {"type", "decision"}, {"origin", origin},
        {"documentGeneration", request->generation}, {"requestId", request->id}, {"allow", true}};
    if (bridge.receive(decision, origin + ".evil/projects") || !bridge.leave.pending()) return false;
    decision["requestId"] = request->id + 1;
    if (bridge.receive(decision, origin) || !bridge.leave.pending()) return false;
    decision["requestId"] = request->id;
    if (!bridge.receive(decision, origin)) return false;
    bridge.unavailable();
    const auto generation = bridge.leave.generation();
    if (!bridge.acceptReady({{"service", "green-atlas-desktop"}, {"protocol", 1}, {"origin", origin}, {"session_token", b}})
        || bridge.leave.generation() != generation) return false;
    return ticketDirectoryId(std::string(32, 'a')) && !ticketDirectoryId("../ticket")
        && privateApiRoute("POST", "/_desktop/handoffs") && !privateApiRoute("GET", "/_desktop/shutdown");
}
}
