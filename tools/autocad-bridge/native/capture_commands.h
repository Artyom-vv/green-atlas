#pragma once
#include "snapshot_writer.h"
#include <functional>

namespace ga::bridge {

void exportProbe();
void exportRegionTopologyProbe();
void exportRegionTopologyFromSourceFile();
ExportResult exportRegionTopologyFromPath(const std::string& sourcePath);
using CaptureObserver = std::function<void(const DrawingGeometry&)>;
bool captureLiveSnapshotForService(const std::string& destination, std::string& error,
                                   const CaptureObserver& observer = {});
#ifdef GA_GEOMETRY_SELFTEST
void geometrySelftest();
#endif

}  // namespace ga::bridge
