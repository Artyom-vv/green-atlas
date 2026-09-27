#pragma once
#include "geometry_types.h"
class AcDbDatabase;

namespace ga::liveQuery {
// Every coverage instance is accounted for, even when display extraction failed.
// Native extents are a broad phase only; never a replacement for membership.
void writeInventory(AcDbDatabase& host, const ga::bridge::DrawingGeometry& drawing,
                    const std::string& destination);
}
