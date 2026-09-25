#pragma once
#include "planar_faces.h"
#include "geometry_types.h"
#include <ostream>

namespace ga::faces {
// Owned by a checked live capture, cleared on close and before a new capture.
// IDs have meaning only with that capture's SHA-verified inventory.
void clearCatalog();
void rememberFaceSources(AcDbDatabase& host,const ga::bridge::DrawingGeometry& drawing);
void prepareFaceLayers(AcDbDatabase& host,const std::set<std::string>& layers,
                       const std::function<bool()>& cancelled = {});
void emitCatalog(std::ostream& out,const std::set<std::string>& layers);
ga::direct::Answer queryFace(AcDbDatabase& host,unsigned id,
                            const std::string& anchor,const AcGePoint3d& point);
std::unique_ptr<AcDbRegion> copyFaceRegion(AcDbDatabase& host,unsigned id,
                                         const std::string& anchor);
}
