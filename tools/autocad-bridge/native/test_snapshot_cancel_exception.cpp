#include "snapshot_writer.h"
#include "operation_control.h"
#include <cassert>
#include <stdexcept>

int main(int argc, char** argv) {
    assert(argc == 2);
    ga::bridge::SnapshotMetadata metadata;
    metadata.sourcePath = "fixture.dwg";
    ga::bridge::ScopedOperationControl control({
        []() -> bool { throw std::runtime_error("control failure"); }, {}
    });
    try {
        ga::bridge::writeTopologySnapshot(metadata, {}, argv[1]);
        return 1;
    } catch (const std::runtime_error&) {
        return 0;
    }
}
