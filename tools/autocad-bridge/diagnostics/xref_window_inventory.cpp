#include "xref_window_inventory.h"
#include "file_io.h"
#include "AcString.h"
#include "dbcurve.h"
#include "dbhatch.h"
#include "dbsymtb.h"
#include <cmath>
#include <iomanip>
#include <limits>
#include <stdexcept>

namespace ga::xref {
namespace {
constexpr unsigned kReportBudget=100000;
std::string q(const std::string& s) { return "\""+ga::bridge::jsonEscape(s)+"\""; }
bool finite(const AcGePoint3d& p) { return std::isfinite(p.x)&&std::isfinite(p.y)&&std::isfinite(p.z); }
void xyz(std::ostream& out,const AcGePoint3d& p) { out<<'['<<p.x<<','<<p.y<<','<<p.z<<']'; }
}
void WindowInventory::read(std::istream& in) {
    if(!(in>>minX>>minY>>maxX>>maxY>>padding)
        ||!std::isfinite(minX)||!std::isfinite(minY)||!std::isfinite(maxX)
        ||!std::isfinite(maxY)||!std::isfinite(padding)||minX>=maxX||minY>=maxY
        ||padding<0||padding>1000) throw std::runtime_error("invalid native window request");
    enabled=true; rows<<std::setprecision(17); failures<<std::setprecision(17);
    in.ignore(std::numeric_limits<std::streamsize>::max(),'\n');
    std::string mode;
    if(std::getline(in,mode)&&!mode.empty()) {
        if(mode!="evaluate"&&mode!="evaluate-points") throw std::runtime_error("invalid window mode");
        queries=std::make_unique<WindowQueries>();
        queries->read(in,minX,minY,maxX,maxY,padding,mode=="evaluate-points");
    }
}
void WindowInventory::inspect(AcDbEntity& entity,const AcGeMatrix3d& transform,
                             const std::string& route,const std::string& context,
                             const std::string& inheritedLayer) {
    if(!enabled) return;
    if(AcDbBlockReference::cast(&entity)) { ++containers; return; } // Children are still all visited.
    ++inspected;
    AcString nativeLayer; entity.layer(nativeLayer);
    const std::string layer=nativeLayer.utf8Str(),type=AcString(entity.isA()->name()).utf8Str();
    const auto separator=layer.find_last_of('|');
    const auto leaf=separator==std::string::npos?layer:layer.substr(separator+1);
    const auto effective=leaf=="0"&&!inheritedLayer.empty()?inheritedLayer:layer;
    // Complete chains must be discovered before individual wall AABBs are
    // discarded. A query point can be inside a building far from every wall.
    if(queries) queries->collectAreaCandidate(entity, route, effective);
    AcDbExtents bounds;
    const auto status=entity.getGeomExtents(bounds);
    if(status==Acad::eOk) bounds.transformBy(transform);
    if(status!=Acad::eOk||!finite(bounds.minPoint())||!finite(bounds.maxPoint())) {
        if(unlocated>=kReportBudget) throw std::runtime_error("unlocated report budget exceeded");
        failures<<(unlocated++?",":"")<<"{\"route\":"<<q(route)<<",\"layer\":"<<q(layer)
            <<",\"effective_layer\":"<<q(effective)<<",\"type\":"<<q(type)
            <<",\"extents_status\":"<<int(status)<<'}';
        return;
    }
    const auto lo=bounds.minPoint(),hi=bounds.maxPoint();
    // Explicit-point windows are search domains, not the native site. Keep the
    // selected site for containment even outside this window. Invalid extents
    // still follow the unlocated/unknown path above; legacy evaluate pins none.
    const bool pinnedSite=queries&&!queries->pinnedSiteRoute().empty()&&route==queries->pinnedSiteRoute();
    if(!pinnedSite&&(hi.x<minX-padding||lo.x>maxX+padding||hi.y<minY-padding||lo.y>maxY+padding)) { ++outside; return; }
    if(near>=kReportBudget) throw std::runtime_error("window report budget exceeded");
    ++layerTypes[effective+":"+type];
    if(queries) queries->inspect(entity,transform,route,effective,lo,hi);
    rows<<(near++?",":"")<<"{\"route\":"<<q(route)<<",\"context\":"<<q(context)
        <<",\"layer\":"<<q(layer)<<",\"effective_layer\":"<<q(effective)<<",\"type\":"<<q(type)
        <<",\"entity_visibility\":"<<int(entity.visibility())<<",\"layer_off\":";
    AcDbLayerTableRecord* layerRecord=nullptr;
    const auto layerStatus=acdbOpenObject(layerRecord,entity.layerId(),AcDb::kForRead);
    if(layerStatus==Acad::eOk&&layerRecord) {
        rows<<(layerRecord->isOff()?"true":"false")<<",\"layer_frozen\":"<<(layerRecord->isFrozen()?"true":"false");
        layerRecord->close();
    } else rows<<"null,\"layer_frozen\":null";
    rows<<",\"bounds\":["; xyz(rows,lo); rows<<','; xyz(rows,hi); rows<<']';
    if(auto* curve=AcDbCurve::cast(&entity)) {
        rows<<",\"native_closed\":"<<(curve->isClosed()?"true":"false");
        AcGePoint3d a,b;
        const auto sa=curve->getStartPoint(a),sb=curve->getEndPoint(b);
        if(sa==Acad::eOk&&sb==Acad::eOk) {
            a.transformBy(transform); b.transformBy(transform);
            if(finite(a)&&finite(b)) {
                rows<<",\"endpoints\":["; xyz(rows,a); rows<<','; xyz(rows,b); rows<<']';
            }
        }
    }
    if(auto* hatch=AcDbHatch::cast(&entity)) rows<<",\"native_hatch_loops\":"<<hatch->numLoops();
    rows<<'}';
}
void WindowInventory::write(std::ostream& out) const {
    out<<"{\"scope\":\"all available leaf instances; native AABB broad phase, not obstacle interpretation\",\"window\":["
        <<minX<<','<<minY<<','<<maxX<<','<<maxY<<"],\"padding\":"<<padding
        <<",\"leaf_instances_inspected\":"<<inspected<<",\"containers_traversed\":"<<containers
        <<",\"near_instances\":"<<near<<",\"outside_instances\":"<<outside<<",\"unlocated_instances\":"<<unlocated
        <<",\"layer_types\":{";
    bool first=true;
    for(const auto& [key,count]:layerTypes) { out<<(first?"":",")<<q(key)<<':'<<count; first=false; }
    out<<"},\"near\":["<<rows.str()<<"],\"unlocated\":["<<failures.str()<<']';
    if(queries) { out<<",\"calculation\":";queries->write(out); }
    out<<'}';
}
}
