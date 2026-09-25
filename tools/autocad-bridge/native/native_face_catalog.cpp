#include "native_face_catalog.h"
#include "planar_faces_internal.h"
#include "xref_instance_access.h"
#include "file_io.h"
#include "cad_utils.h"
#include "AcString.h"
#include "dbcurve.h"
#include <algorithm>
#include <map>
#include <stdexcept>

namespace ga::faces {
namespace {
struct Entry {std::string layer,anchor;Face face;};
AcDbDatabase* owner=nullptr;
std::vector<Entry> catalog;
std::set<std::string> linearRoutes;
std::vector<std::string> issues;
ga::bridge::DrawingGeometry captured;
std::set<std::string> preparedLayers;
std::map<std::string,std::string> routeLayers;
std::string q(const std::string& s) {return "\""+ga::bridge::jsonEscape(s)+"\"";}
std::string routeOf(const ga::bridge::EntityCoverage& row) {
    std::string result;for(const auto& parent:row.instanceChain) result+=parent+"/";
    return result+row.handle;
}
bool clipLayer(const std::string& name) {
    AcString lower(name.c_str());lower.makeLower();
    const std::string text=lower.utf8Str();
    // Source cut boundaries, NOT arbitrary crossing networks or page frames.
    return text.find("граница заказа")!=std::string::npos
        ||text.find("граница проектирования")!=std::string::npos;
}
std::unique_ptr<AcDbCurve> clone(const AcDbCurve& curve) {
    auto* copy=AcDbCurve::cast(curve.clone());
    if(!copy) throw std::runtime_error("native face source clone failed");
    return std::unique_ptr<AcDbCurve>(copy);
}
bool overlap(const AcDbExtents& a,const AcDbExtents& b,double gap) {
    return a.minPoint().x<=b.maxPoint().x+gap&&a.maxPoint().x>=b.minPoint().x-gap
        &&a.minPoint().y<=b.maxPoint().y+gap&&a.maxPoint().y>=b.minPoint().y-gap;
}
void point(std::ostream& out,const AcGePoint3d& p) {out<<'['<<p.x<<','<<p.y<<','<<p.z<<']';}
void strings(std::ostream& out,const std::set<std::string>& values) {
    out<<'[';bool first=true;for(const auto& s:values) {out<<(first?"":",")<<q(s);first=false;}out<<']';
}
}
void clearCatalog() {
    catalog.clear();linearRoutes.clear();issues.clear();owner=nullptr;
    captured={};preparedLayers.clear();routeLayers.clear();
}
void rememberFaceSources(AcDbDatabase& host,const ga::bridge::DrawingGeometry& drawing) {
    clearCatalog();owner=&host;
    captured.coverage=drawing.coverage;
    for(const auto& row:drawing.coverage) routeLayers[routeOf(row)]=row.layer;
    for(const auto& region:drawing.regions) if(region.resolved&&region.sourceHandles.empty()) {
        ga::bridge::RegionTopology identity;
        identity.measurement.handle=region.measurement.handle;
        identity.instanceChain=region.instanceChain;identity.resolved=true;
        captured.regions.push_back(std::move(identity));
    }
}
void prepareFaceLayers(AcDbDatabase& host,const std::set<std::string>& requested,
                    const std::function<bool()>& cancelled) {
    if(owner!=&host) throw std::runtime_error("native faces require a checked capture");
    std::set<std::string> layers;
    std::set_difference(requested.begin(),requested.end(),preparedLayers.begin(),preparedLayers.end(),std::inserter(layers,layers.end()));
    if(layers.empty()) return;
    struct Rollback {
        std::size_t entries=catalog.size(), notices=issues.size();
        std::set<std::string> lines=linearRoutes;
        bool committed=false;
        ~Rollback() {if(!committed) {catalog.resize(entries);issues.resize(notices);linearRoutes=std::move(lines);}}
    } rollback;
    const auto& drawing=captured;
    const double metres=ga::bridge::unitToMetres(host.insunits());
    if(!std::isfinite(metres)||metres<=0) {
        issues.push_back("native face units unavailable");return;
    }
    using Scope=std::pair<std::string,std::string>;
    std::map<Scope,std::vector<Source>> scopes;
    std::map<std::string,std::vector<Source>> boundaries;
    std::set<std::string> authoredAreas;
    for(const auto& region:drawing.regions) if(region.resolved&&region.sourceHandles.empty()) {
        std::string route;for(const auto& parent:region.instanceChain) route+=parent+"/";
        authoredAreas.insert(route+region.measurement.handle);
    }
    for(const auto& row:drawing.coverage) {
        checkCancelled(cancelled);
        if(row.status=="context"||(!layers.count(row.layer)&&!clipLayer(row.layer))) continue;
        const auto route=routeOf(row);
        try {
            ga::xref::Instance instance;ga::xref::resolve(host,route,instance);
            auto* curve=AcDbCurve::cast(instance.entity);
            if(!curve) continue;
            AcDbEntity* transformed=nullptr;
            require(curve->getTransformedCopy(instance.transform,transformed),"native face world clone");
            std::unique_ptr<AcDbEntity> entity(transformed);
            auto* world=AcDbCurve::cast(entity.get());
            if(!world) throw std::runtime_error("native transformed curve unavailable");
            const auto slash=route.find_last_of('/');
            const auto parent=slash==std::string::npos?"":route.substr(0,slash);
            if(clipLayer(row.layer)) {
                boundaries[parent].push_back({route,row.layer,clone(*world),true});
            }
            if(!layers.count(row.layer)) continue;
            AcGePoint3d start,end;
            // A readable curve without an authored area remains a distance
            // obstacle, including a closed-flag polyline whose REGION failed.
            // Every later position still requires a successful native distance.
            if(!authoredAreas.count(route)
                &&curve->getStartPoint(start)==Acad::eOk&&curve->getEndPoint(end)==Acad::eOk)
                linearRoutes.insert(route);
            entity.release();
            scopes[{parent,row.layer}].push_back({route,row.layer,std::unique_ptr<AcDbCurve>(world),false,!authoredAreas.count(route)});
        } catch(const std::exception& error) {issues.push_back(route+": "+error.what());}
    }
    for(auto& [scope,sources]:scopes) {
        checkCancelled(cancelled);
        try {
            if(std::none_of(sources.begin(),sources.end(),[](const auto& source) {return source.needsArea;})) continue;
            AcDbExtents extent;
            bool hasBounds=false;
            for(const auto& source:sources) {
                AcDbExtents b;if(source.curve->getGeomExtents(b)==Acad::eOk) {
                    if(hasBounds) extent.addExt(b);else {extent=b;hasBounds=true;}
                }
            }
            if(hasBounds&&!clipLayer(scope.second)) for(const auto& boundary:boundaries[scope.first]) {
                AcDbExtents b;
                if(boundary.curve->getGeomExtents(b)==Acad::eOk&&overlap(extent,b,kRepairGapMetres/metres))
                    sources.push_back({boundary.route,boundary.layer,clone(*boundary.curve),true});
            }
            auto result=assemble(std::move(sources),1/metres,cancelled);
            // Nested disconnected loops may be courtyards. Do not silently
            // fill them; keep their original native objects and an explicit issue.
            std::set<std::size_t> nested;
            for(std::size_t i=0;i<result.faces.size();++i) for(std::size_t j=0;j<i;++j) {
                const auto& a=result.faces[i];const auto& b=result.faces[j];
                const auto ea=a.query->evidence(),eb=b.query->evidence();
                if(ea.low.x>eb.high.x||eb.low.x>ea.high.x||ea.low.y>eb.high.y||eb.low.y>ea.high.y) continue;
                const auto ab=a.query->queryPlanar(b.display.front()),ba=b.query->queryPlanar(a.display.front());
                if(ab.membership=="occupied"||ba.membership=="occupied") {nested.insert(i);nested.insert(j);}
            }
            for(std::size_t i=0;i<result.faces.size();++i) {
                auto& face=result.faces[i];
                if(nested.count(i)) {issues.push_back(*face.physicalRoutes.begin()+": nested native faces require hole review");continue;}
                const auto anchor=*face.physicalRoutes.begin();
                catalog.push_back({scope.second,anchor,std::move(face)});
            }
            issues.insert(issues.end(),result.issues.begin(),result.issues.end());
        } catch(const std::exception& error) {
            checkCancelled(cancelled);
            issues.push_back(scope.second+": "+error.what());
        }
    }
    preparedLayers.insert(layers.begin(),layers.end());
    rollback.committed=true;
}
void emitCatalog(std::ostream& out,const std::set<std::string>& layers) {
    std::set<std::string> selectedLines;
    for(const auto& route:linearRoutes) if(layers.count(routeLayers.at(route))) selectedLines.insert(route);
    out<<",\"face_policy\":"<<q(kPolicy)<<",\"linear_routes\":";strings(out,selectedLines);
    out<<",\"face_issues\":[";
    for(std::size_t i=0;i<issues.size();++i) out<<(i?",":"")<<q(issues[i]);
    out<<"],\"faces\":[";
    bool first=true;
    for(std::size_t i=0;i<catalog.size();++i) {
        const auto& entry=catalog[i];const auto& face=entry.face;const auto& e=face.query->evidence();
        if(!layers.count(entry.layer)) continue;
        out<<(first?"":",")<<"{\"id\":"<<i+1<<",\"anchor\":"<<q(entry.anchor)<<",\"layer\":"<<q(entry.layer)
            <<",\"routes\":";strings(out,face.routes);
        first=false;
        out<<",\"physical_routes\":";strings(out,face.physicalRoutes);
        out<<",\"area\":"<<e.localArea*e.jacobian<<",\"bounds\":["<<e.low.x<<','<<e.low.y<<','<<e.high.x<<','<<e.high.y
            <<"],\"display\":[";
        for(std::size_t p=0;p<face.display.size();++p) {out<<(p?",":"");point(out,face.display[p]);}
        out<<"],\"repairs\":[";
        for(std::size_t r=0;r<face.repairs.size();++r) {
            const auto& repair=face.repairs[r];out<<(r?",":"")<<"{\"from\":"<<q(repair.from)<<",\"to\":"<<q(repair.to)<<",\"a\":";
            point(out,repair.a);out<<",\"b\":";point(out,repair.b);out<<'}';
        }
        out<<"]}";
    }
    out<<']';
}
ga::direct::Answer queryFace(AcDbDatabase& host,unsigned id,
                            const std::string& anchor,const AcGePoint3d& point) {
    if(owner!=&host||id==0||id>catalog.size()||catalog[id-1].anchor!=anchor)
        throw std::runtime_error("native face does not belong to the active capture");
    return catalog[id-1].face.query->queryPlanar(point);
}
std::unique_ptr<AcDbRegion> copyFaceRegion(AcDbDatabase& host,unsigned id,const std::string& anchor) {
    if(owner!=&host||id==0||id>catalog.size()||catalog[id-1].anchor!=anchor)
        throw std::runtime_error("native face does not belong to the active capture");
    return catalog[id-1].face.query->copyWorldRegion();
}
}
