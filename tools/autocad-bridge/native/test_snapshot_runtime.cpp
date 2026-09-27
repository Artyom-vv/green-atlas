// Runs without AutoCAD or ObjectARX. Exercises the real writer/control modules.
#include "snapshot_writer.h"
#include "operation_control.h"
#include "geometry_math.h"
#include "file_io.h"
#include <cassert>
#include <cmath>
#include <iostream>
#include <stdexcept>

using namespace ga::bridge;

DrawingGeometry sampleGeometry() {
    DrawingGeometry geometry;
    const std::string layer = "Подоснова|Здание\"\\\n";
    const std::vector<std::string> chain{"B1", "MINSERT:B2:R1:C2"};
    RegionTopology region;
    region.measurement = {"A1", layer, 11, 18, true, true};
    region.sourceLayer = "Здание";
    region.instanceChain = chain;
    region.resolved = true;
    region.loops = {
        {"outer", {{0,0,0}, {4,0,0}, {4,3,0}, {0,3,0}, {0,0,0}}, 0},
        {"hole", {{1,1,0}, {2,1,0}, {2,2,0}, {1,2,0}, {1,1,0}}, 0}
    };
    geometry.regions.push_back(region);
    NativePath path;
    path.handle = "A2";
    path.sourceLayer = "Сети";
    path.layer = "Подоснова|Сети";
    path.instanceChain = chain;
    path.coordinates = {{0.12345678901234567, 0, 1}, {5, 8, 1}};
    path.resolved = true;
    path.sampledMaximumDeviation = 0.00001;
    geometry.paths.push_back(path);
    geometry.points.push_back({"A3", "Деревья", "Подоснова|Деревья", chain, {2,3,4}});
    geometry.coverage = {
        {"A1", "AcDbHatch", region.sourceLayer, layer, chain, "native", "hatch", "", ""},
        {"A2", "AcDbLine", path.sourceLayer, path.layer, chain, "native", "curve", "", ""},
        {"A3", "AcDbPoint", "Деревья", "Подоснова|Деревья", chain, "native", "point", "", ""},
        {"A4", "AcDbText", "Текст", "Текст", {}, "context", "annotation", "annotation only", ""},
        {"A5", "AcDbHatch", "Газон", "Газон", {}, "unresolved", "hatch", "test unresolved", ""},
        {"A6", "AcDbBlockReference", "0", "0", {}, "context", "xref", "", "xref/X1"}
    };
    auto& traversal = geometry.traversal;
    traversal.visitedEntities = 6;
    traversal.blockReferences = traversal.traversedBlockReferences = 1;
    traversal.xrefBlockReferences = 1;
    traversal.xrefDependencies["X1"] = {"X1", "Подоснова", "../base.dwg",
        "/package/base.dwg", std::string(64, 'b'), 42, "resolved"};
    return geometry;
}

void checkControlScopes() {
    assert(!operationCancelled());
    int observed = 0;
    {
        ScopedOperationControl outer({[] { return true; },
            [&](const std::string& phase, std::size_t count) {
                assert(phase == "collecting"); observed += static_cast<int>(count);
            }});
        assert(operationCancelled());
        reportOperationProgress("collecting", 3);
        try {
            ScopedOperationControl inner({[] { return false; }, {}});
            assert(!operationCancelled());
            reportOperationProgress("collecting", 100);
            throw std::runtime_error("unwind test");
        } catch (const std::runtime_error&) {}
        assert(operationCancelled());
        reportOperationProgress("collecting", 4);
    }
    assert(observed == 7);
    assert(!operationCancelled());
    reportOperationProgress("ignored", 100);
    assert(observed == 7);
}

int main(int argc, char** argv) {
    assert(argc == 4 || argc == 5);
    checkControlScopes();
    assert(pointDistance({0,0,0}, {0,3,4}) == 5);
    assert(projectedPointDistance({0,0,10}, {3,4,99}) == 5);
    assert(pointSegmentDistance({2,3,0}, {0,0,0}, {4,0,0}) == 3);
    assert(!finitePoint({NAN,0,0}));
    const int cancelAt = std::stoi(argv[2]);
    const bool live = std::string(argv[3]) == "live";
    int polls = 0;
    ScopedOperationControl control({[&] { return cancelAt > 0 && ++polls >= cancelAt; }, {}});
    SnapshotMetadata metadata{argv[1], std::string(64, 'a'), "revision-1", 6, 1,
                              live ? 32 : 0, !live};
    const auto result = writeTopologySnapshot(
        metadata, sampleGeometry(), argc == 5 ? argv[4] : "");
    std::cout << "{\"success\":" << (result.success ? "true" : "false")
              << ",\"path\":\"" << jsonEscape(result.path)
              << "\",\"error\":\"" << jsonEscape(result.error) << "\"}\n";
}
