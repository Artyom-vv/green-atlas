#pragma once
#include "geometry_types.h"

namespace ga::bridge {

struct SnapshotMetadata {
    std::string sourcePath;
    std::string sourceHash;
    std::string revision;
    int unitsCode = 0;
    double metresPerUnit = 0;
    int databaseModificationFlags = 0;
    bool sideDatabaseCapture = false;
};

struct ExportResult {
    bool success = false;
    std::string path;
    std::string error;
};

// Pure snapshot publication: no AutoCAD API, file loading, XREF repair or UI.
// Field order/precision and cancellation checkpoints are protocol-compatible.
ExportResult writeTopologySnapshot(const SnapshotMetadata& metadata,
                                   const DrawingGeometry& geometry,
                                   const std::string& destination = {});

}  // namespace ga::bridge
