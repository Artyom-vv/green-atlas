#include "live_inventory.h"
#include "xref_instance_access.h"
#include "native_area_candidates.h"
#include "native_face_catalog.h"
#include "file_io.h"
#include "operation_control.h"
#include "dbcurve.h"
#include <cmath>
#include <iomanip>
#include <set>
#include <sstream>
#include <stdexcept>

namespace ga::liveQuery {
namespace {
std::string q(const std::string& s) { return "\""+ga::bridge::jsonEscape(s)+"\""; }
bool finite(const AcGePoint3d& p) {
    return std::isfinite(p.x)&&std::isfinite(p.y)&&std::isfinite(p.z);
}
std::string routeOf(const ga::bridge::EntityCoverage& row) {
    std::string route;
    for(const auto& parent:row.instanceChain) route+=parent+"/";
    return route+row.handle;
}
}
void writeInventory(AcDbDatabase& host, const ga::bridge::DrawingGeometry& drawing,
                    const std::string& destination) {
    std::ostringstream out;
    out<<std::setprecision(17)<<"{\"schema\":\"green-atlas.live-inventory/1\",\"objects\":[";
    ga::nativeQuery::AreaCandidates candidates;
    std::set<std::string> seen;
    bool first=true;
    for(const auto& row:drawing.coverage) {
        if(ga::bridge::operationCancelled()) throw std::runtime_error("live_inventory_cancelled");
        const auto route=routeOf(row);
        if(!seen.insert(route).second) throw std::runtime_error("live_inventory_duplicate_instance");
        const bool context=row.status=="context";
        out<<(first?"":",")<<"{\"route\":"<<q(route)<<",\"layer\":"<<q(row.layer)
            <<",\"entity_type\":"<<q(row.entityType)<<",\"context\":"<<(context?"true":"false")
            <<",\"bounds\":";
        first=false;
        std::string error;
        bool curve=false;
        try {
            if(context) { out<<"null"; }
            else {
                ga::xref::Instance instance;
                ga::xref::resolve(host,route,instance);
                curve=AcDbCurve::cast(instance.entity)!=nullptr;
                // Collect all same-instance cycles before any spatial filtering.
                candidates.consider(*instance.entity,route);
                AcDbExtents bounds;
                const auto status=instance.entity->getGeomExtents(bounds);
                if(status!=Acad::eOk) throw std::runtime_error("native_extents_unavailable");
                bounds.transformBy(instance.transform);
                const auto lo=bounds.minPoint(),hi=bounds.maxPoint();
                if(!finite(lo)||!finite(hi)||lo.x>hi.x||lo.y>hi.y)
                    throw std::runtime_error("native_extents_invalid");
                out<<'['<<lo.x<<','<<lo.y<<','<<hi.x<<','<<hi.y<<']';
            }
        } catch(const std::exception& failure) { out<<"null"; error=failure.what(); }
        out<<",\"curve\":"<<(curve?"true":"false")<<",\"error\":"<<q(error)<<'}';
    }
    out<<"],\"groups\":[";
    first=true;
    for(const auto& group:candidates.collect()) {
        out<<(first?"":",")<<"{\"routes\":[";
        first=false;
        for(std::size_t i=0;i<group.routes.size();++i) out<<(i?",":"")<<q(group.routes[i]);
        out<<"],\"error\":"<<q(group.error)<<'}';
    }
    out<<']';
    ga::faces::rememberFaceSources(host,drawing);
    out<<",\"face_preparation_available\":true";
    out<<'}';
    if(!ga::bridge::writeAtomicText(destination,out.str()))
        throw std::runtime_error("live_inventory_write_failed");
}
}
