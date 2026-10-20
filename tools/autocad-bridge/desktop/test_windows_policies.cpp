#include "windows-policies.h"
#include <cassert>
#include <iostream>
using namespace ga::desktop;
using namespace ga::desktop::windows;
int main() {
    assert(selfTest());
    Bridge picker; picker.origin = "http://127.0.0.1:8123"; picker.runtime = true;
    picker.newDocument(); picker.leave.ready();
    Json choice{{"schemaVersion", 1}, {"type", "chooseDependency"}, {"origin", picker.origin},
        {"documentGeneration", picker.leave.generation()}, {"requestId", 1}, {"field", "blender"}};
    assert(dependencyPickerRequest(choice, picker.origin + "/projects", picker));
    assert(!dependencyPickerRequest(choice, "https://foreign.example", picker));
    auto invalidChoice = choice; invalidChoice["field"] = "arbitrary";
    assert(!dependencyPickerRequest(invalidChoice, picker.origin, picker));
    invalidChoice = choice; invalidChoice["documentGeneration"] = 999;
    assert(!dependencyPickerRequest(invalidChoice, picker.origin, picker));
    invalidChoice = choice; invalidChoice["requestId"] = true;
    assert(!dependencyPickerRequest(invalidChoice, picker.origin, picker));
    picker.unavailable(); assert(!dependencyPickerRequest(choice, picker.origin, picker));
    const Json config{{"schema", "green-atlas.desktop-launch/1"}, {"kind", "windows_webview2"},
        {"host", "GreenAtlasDesktop.exe"}, {"runtime", "Runtime/GreenAtlasRuntime.exe"}, {"web", "Web"}, {"worker", "Worker"}};
    assert(launchMetadata(config));
    for (const auto& key : {"host", "runtime", "web", "worker"}) {
        auto bad = config; bad[key] = "../outside"; assert(!launchMetadata(bad));
    }
    auto bad = config; bad["token"] = "secret"; assert(!launchMetadata(bad));
    const std::string origin = "http://127.0.0.1:8123";
    for (const auto& badOrigin : {origin + "@evil.test", origin + ".evil.test", origin + "?x=1", origin + "#x"}) assert(!sameOrigin(origin, badOrigin));
    assert(externalHttpUrl("https://example.org/resource?q=1#reference"));
    for (const auto* invalid : {"file:///tmp/file", "javascript:alert(1)", "https://user:secret@example.org", "https://example.org\\evil", "https://example.org/\nsecret", "https:///path"}) assert(!externalHttpUrl(invalid));
    assert(supportedWebViewVersion("152.0.4191.53") && supportedWebViewVersion("153.0.1.1"));
    for (const auto* invalid : {"151.9.9999.9999", "152.0.4191.52", "152.0.4191.53 beta", "152.0.4191", "152.0.4191.53.1", "152..4191.53"}) assert(!supportedWebViewVersion(invalid));
    Bridge malformed;
    assert(!malformed.acceptReady({{"service", 1}, {"protocol", 1}, {"origin", origin}, {"session_token", std::string(43, 'x')}}));
    malformed.origin = origin;
    assert(!malformed.receive({{"schemaVersion", 1}, {"type", true}, {"origin", origin}}, origin));
    Bridge failed;
    failed.origin = origin; failed.newDocument(); failed.leave.ready();
    const auto pending = failed.leave.begin(Action::close); assert(pending);
    failed.failedDocument(); assert(failed.documentFailed && !failed.leave.pending());
    assert(!failed.receive({{"schemaVersion", 1}, {"type", "ready"}, {"origin", origin}}, origin));
    assert(!failed.receive({{"schemaVersion", 1}, {"type", "decision"}, {"origin", origin},
        {"documentGeneration", pending->generation}, {"requestId", pending->id}, {"allow", true}}, origin));
    assert(failed.acceptReady({{"service", "green-atlas-desktop"}, {"protocol", 1}, {"origin", origin}, {"session_token", std::string(43, 'x')}}));
    assert(failed.documentFailed);
    failed.newDocument(); assert(!failed.documentFailed && failed.leave.generation() > pending->generation);
    assert(failed.receive({{"schemaVersion", 1}, {"type", "ready"}, {"origin", origin}}, origin));
    const std::string id = "12345678-1234-1234-1234-123456789abc";
    const auto png = "/api/projects/" + id + "/scene-renders/" + id + "/image";
    const auto zip = "/api/projects/" + id + "/releases/release-1/artifacts/bundle";
    const auto source = "/api/projects/" + id + "/source-dxf/download";
    assert(downloadType(source, "application/dxf", "source.dxf"));
    assert(sourceDxfRoute(source + "?expected_source_sha256=" + std::string(64, 'a')));
    assert(!sourceDxfRoute(source + "?expected_source_sha256=" + std::string(64, 'A')));
    assert(!sourceDxfRoute(source + "?other=1") && !sourceDxfRoute(source + "/"));
    assert(!downloadType(source, "application/zip", "source.zip"));
    assert(downloadType(png, "image/png", "render.png"));
    assert(downloadType(zip, "application/zip", "result.zip"));
    Bridge session; session.origin = origin; session.token = std::string(43, 's'); session.runtime = true;
    DownloadOffer offered{7, origin, session.token, zip, "application/zip", "result.zip"};
    assert(offered.current(7, session, false));
    assert(!offered.current(8, session, false) && !offered.current(7, session, true));
    session.token = std::string(43, 't'); assert(!offered.current(7, session, false));
    session.token = offered.token; session.failedDocument(); assert(!offered.current(7, session, false));
    session.newDocument(); assert(offered.current(7, session, false));
    session.unavailable(); assert(!offered.current(7, session, false));
    assert(downloadType(zip, "text/csv; charset=utf-8", "schedule.csv"));
    assert(!downloadType(png, "application/zip", "render.png"));
    assert(!downloadType(zip + "?secret=1", "application/zip", "result.zip"));
    assert(!downloadType(zip, "application/zip", "../result.zip"));
    assert(!downloadType(zip, "application/zip", "result.zip:stream"));
    for (const auto* reserved : {"NUL.zip", "con.zip", "COM1.zip", "lpt9.zip"}) assert(!downloadType(zip, "application/zip", reserved));
    assert(!safeInteger(true) && !safeInteger(1.0) && !safeInteger(0));
    assert(safeInteger(0, true) && !safeInteger(9007199254740992ULL));
    assert(handoffId(std::string(64, 'a')) && !handoffId(std::string(64, 'A')));
    assert(privateApiRoute("POST", "/_desktop/handoffs/" + std::string(64, 'a') + "/resume"));
    assert(!privateApiRoute("POST", "/_desktop/handoffs?retry_failed=1"));
    std::cout << Json{{"schema", "green-atlas.desktop-self-test/1"}, {"passed", true}, {"real_windows", false}}.dump() << '\n';
}
