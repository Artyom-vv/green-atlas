#include "contracts.h"
#include <cassert>

int main() {
    assert(ga::desktop::renderImageRoute("/api/projects/12345678-1234-1234-1234-123456789abc/scene-renders/abcdefab-1234-1234-1234-123456789abc/image"));
    assert(!ga::desktop::renderImageRoute("/api/projects/12345678-1234-1234-1234-123456789abc/scene-renders/not-an-id/image"));
    assert(!ga::desktop::renderImageRoute("/api/projects/12345678-1234-1234-1234-123456789abc/scene-renders/abcdefab-1234-1234-1234-123456789abc/image/extra"));

    using namespace ga::desktop;
    const std::string prefix = "/projects/122a1ecd-4e1f-4a4e-bf88-13b93299ae1f/";
    assert(handoffRoute("Drawing.autocad.json", prefix + "setup"));
    assert(handoffRoute("Drawing.autocad.json", prefix + "setup?open=editor"));
    assert(projectRoute(prefix + "setup?open=editor"));
    assert(!handoffRoute("Drawing.dxf", prefix + "setup?open=editor"));
    assert(handoffRoute("Drawing.dxf", prefix + "import?source=cad"));
    assert(!handoffRoute("Drawing.autocad.json", prefix + "import?source=cad"));
    assert(projectRoute("/projects") && projectRoute(prefix + "workspace"));
    for (const auto& bad : {prefix + "setup\n", prefix + "../setup", prefix + "workspace#x", prefix + "setup?x=1", std::string("https://example.com") + prefix + "setup"})
        assert(!projectRoute(bad));
    for (const auto& bad : {prefix + "setup?open=editor&x=1", prefix + "setup?open=editor#x", prefix + "setup?open=other", prefix + "setup?open=editor\n"}) {
        assert(!projectRoute(bad));
        assert(!handoffRoute("Drawing.autocad.json", bad));
    }
    assert(!canonicalUuid("------------------------------------"));
    assert(ticketDirectoryId(std::string(32, 'a')));
    assert(!ticketDirectoryId(std::string(31, 'a')) && !ticketDirectoryId(std::string(32, 'G')));
    const std::string artifact = "/api" + prefix + "releases/release-1/artifacts/release-1-cad";
    assert(releaseArtifactRoute(artifact));
    assert(!releaseArtifactRoute(artifact + "?x=1") && !releaseArtifactRoute(artifact + "/.."));
    const std::string origin = "http://127.0.0.1:8123", token(43, 'a'), rotated(43, 'b');
    assert(loopbackPort(origin) == 8123 && loopbackPort("http://127.0.0.1:65535") == 65535);
    for (const auto& bad : {"http://127.0.0.1:08123", "http://127.0.0.1:80", "http://127.0.0.1:65536", "http://127.0.0.1:8123/", "http://127.0.0.1:8123?x=1", "http://localhost:8123", "https://127.0.0.1:8123"}) assert(!loopbackPort(bad));
    assert(runtimeSessionReady("", "", origin, token));
    assert(runtimeSessionReady(origin, token, origin, rotated));
    assert(!runtimeSessionReady(origin, token, "http://127.0.0.1:8124", rotated));
    assert(!runtimeSessionReady(origin, token, origin, token));
    assert(!runtimeSessionReady(origin, token, origin, rotated + "\n"));
    LeaveCoordinator leave;
    leave.newDocument();
    assert(!leave.begin(Action::close));
    leave.ready();
    auto first = leave.begin(Action::handoff, prefix + "setup");
    assert(first && !leave.begin(Action::close));
    assert(!leave.decide(first->generation + 1, first->id, true) && leave.pending());
    assert(!leave.decide(first->generation, first->id, false) && !leave.pending());
    first = leave.begin(Action::close);
    leave.newDocument(); leave.ready();
    auto second = leave.begin(Action::quit);
    assert(!leave.decide(first->generation, first->id, true) && leave.pending());
    auto decision = leave.decide(second->generation, second->id, true);
    assert(decision && decision->action == Action::quit && !leave.pending());
}
