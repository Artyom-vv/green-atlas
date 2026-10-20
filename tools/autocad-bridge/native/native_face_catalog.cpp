#include "native_face_catalog.h"
#include "planar_faces_internal.h"
#include "xref_instance_access.h"
#include "file_io.h"
#include "cad_utils.h"
#include "AcString.h"
#include "dbcurve.h"
#include "dbhatch.h"
#include "bridge_config.h"
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
std::set<std::string> preparedCompanions;
bool typedTopology = false;
std::map<std::string,std::string> routeLayers;
std::map<std::string,std::string> archiveRoutes;
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
std::vector<std::unique_ptr<AcDbCurve>> areaBoundaryEdges(const ga::xref::Instance& instance) {
    // AutoCAD извлекает границы на временных объектах; исходный HATCH/REGION не меняется.
    if(!AcDbRegion::cast(instance.entity)&&!AcDbHatch::cast(instance.entity))
        throw std::runtime_error("face companion has no supported native boundary");
    std::unique_ptr<AcDbEntity> source(AcDbEntity::cast(instance.entity->clone()));
    if(!source) throw std::runtime_error("native boundary source clone failed");
    ga::direct::Prepared local;
    auto* region=ga::direct::prepareLocalArea(source.get(),local);
    AcDbEntity* transformed=nullptr;
    require(region->getTransformedCopy(instance.transform,transformed),"native boundary world clone");
    std::unique_ptr<AcDbEntity> world(transformed);
    auto* worldRegion=AcDbRegion::cast(world.get());
    if(!worldRegion) throw std::runtime_error("native boundary transformed region unavailable");
    AcDbVoidPtrArray parts;
    const auto status=worldRegion->explode(parts);
    std::vector<std::unique_ptr<AcDbEntity>> owned;
    for(void* raw:parts) owned.emplace_back(static_cast<AcDbEntity*>(raw));
    require(status,"native boundary region explode");
    if(owned.empty()||owned.size()>ga::bridge::kMaximumTopologyElementsPerEntity)
        throw std::runtime_error("native boundary edge count invalid");
    std::vector<std::unique_ptr<AcDbCurve>> edges;
    for(auto& entity:owned) {
        auto* curve=AcDbCurve::cast(entity.get());
        if(!curve) throw std::runtime_error("native boundary explode returned noncurve");
        entity.release();edges.emplace_back(curve);
    }
    return edges;
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
    preparedCompanions.clear();typedTopology=false;
    archiveRoutes.clear();
}
void rememberFaceSources(AcDbDatabase& host,const ga::bridge::DrawingGeometry& drawing,
                         const std::map<std::string,std::string>& aliases) {
    clearCatalog();owner=&host;
    archiveRoutes=aliases;
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
                    const std::function<bool()>& cancelled,
                    const std::set<std::string>* companionLayers) {
    if(owner!=&host) throw std::runtime_error("native faces require a checked capture");
    if(companionLayers) {
        for(const auto& layer:*companionLayers)
            if(requested.count(layer)) throw std::runtime_error("face companion is also a physical layer");
        if((typedTopology||!preparedLayers.empty())&&(!typedTopology||preparedCompanions!=*companionLayers))
            throw std::runtime_error("face companions changed without a fresh preparation");
    } else if(typedTopology) throw std::runtime_error("face topology policy changed without a fresh preparation");
    std::set<std::string> layers;
    std::set_difference(requested.begin(),requested.end(),preparedLayers.begin(),preparedLayers.end(),std::inserter(layers,layers.end()));
    if(layers.empty()&&!companionLayers) return;
    struct Rollback {
        std::size_t entries=catalog.size(), notices=issues.size();
        std::set<std::string> lines=linearRoutes;
        std::set<std::string> companions=preparedCompanions;
        bool typed=typedTopology;
        bool committed=false;
        ~Rollback() {if(!committed) {catalog.resize(entries);issues.resize(notices);linearRoutes=std::move(lines);
            preparedCompanions=std::move(companions);typedTopology=typed;}}
    } rollback;
    if(companionLayers) {typedTopology=true;preparedCompanions=*companionLayers;}
    const auto& drawing=captured;
    const double metres=ga::bridge::unitToMetres(host.insunits());
    if(!std::isfinite(metres)||metres<=0) {
        issues.push_back("native face units unavailable");return;
    }
    using Scope=std::pair<std::string,std::string>;
    std::map<Scope,std::vector<Source>> scopes;
    std::map<std::string,std::vector<Source>> boundaries;
    std::set<std::string> authoredAreas;
    const auto clipping=[&](const std::string& name) {
        return companionLayers?companionLayers->count(name)!=0:clipLayer(name);
    };
    for(const auto& region:drawing.regions) if(region.resolved&&region.sourceHandles.empty()) {
        std::string route;for(const auto& parent:region.instanceChain) route+=parent+"/";
        authoredAreas.insert(route+region.measurement.handle);
    }
    for(const auto& row:drawing.coverage) {
        checkCancelled(cancelled);
        if(row.status=="context"||(!layers.count(row.layer)&&!clipping(row.layer))) continue;
        const auto route=routeOf(row);
        try {
            const auto alias=archiveRoutes.find(route);
            if(!archiveRoutes.empty()&&alias==archiveRoutes.end())
                throw std::runtime_error("captured face source has no archive identity");
            ga::xref::Instance instance;
            ga::xref::resolve(host,alias==archiveRoutes.end()?route:alias->second,instance);
            auto* curve=AcDbCurve::cast(instance.entity);
            if(!curve) {
                if(companionLayers&&clipping(row.layer)) {
                    const auto slash=route.find_last_of('/');
                    const auto parent=slash==std::string::npos?"":route.substr(0,slash);
                    auto edges=areaBoundaryEdges(instance);
                    for(auto& edge:edges) boundaries[parent].push_back({route,row.layer,std::move(edge),true});
                }
                continue;
            }
            AcDbEntity* transformed=nullptr;
            require(curve->getTransformedCopy(instance.transform,transformed),"native face world clone");
            std::unique_ptr<AcDbEntity> entity(transformed);
            auto* world=AcDbCurve::cast(entity.get());
            if(!world) throw std::runtime_error("native transformed curve unavailable");
            const auto slash=route.find_last_of('/');
            const auto parent=slash==std::string::npos?"":route.substr(0,slash);
            if(clipping(row.layer)) {
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
            if(hasBounds&&!clipping(scope.second)) for(const auto& boundary:boundaries[scope.first]) {
                AcDbExtents b;
                if(boundary.curve->getGeomExtents(b)==Acad::eOk&&overlap(extent,b,kRepairGapMetres/metres))
                    sources.push_back({boundary.route,boundary.layer,clone(*boundary.curve),true});
            }
            auto result=assemble(std::move(sources),1/metres,cancelled);
            // Nested disconnected loops may be courtyards. Do not silently
            // fill them; keep their original native objects and an explicit issue.
            std::set<std::size_t> nested;
            for(std::size_t i=0;i<result.faces.size();++i) for(std::size_t j=0;j<i;++j) {
                if((j&255)==0) checkCancelled(cancelled);
                const auto& a=result.faces[i];const auto& b=result.faces[j];
                const auto ea=a.query->evidence(),eb=b.query->evidence();
                if(ea.low.x>eb.high.x||eb.low.x>ea.high.x||ea.low.y>eb.high.y||eb.low.y>ea.high.y) continue;
                const auto ab=a.query->membershipPlanar(b.display.front()),ba=b.query->membershipPlanar(a.display.front());
                if(ab.membership=="occupied"||ba.membership=="occupied") {nested.insert(i);nested.insert(j);}
            }
            for(std::size_t i=0;i<result.faces.size();++i) {
                auto& face=result.faces[i];
                if(companionLayers) {
                    // Совпадающее ребро хранит все источники, но физическая роль
                    // принадлежит только объектам текущего расчётного слоя.
                    for(auto route=face.physicalRoutes.begin();route!=face.physicalRoutes.end();) {
                        if(routeLayers.at(*route)!=scope.second) route=face.physicalRoutes.erase(route);
                        else ++route;
                    }
                    if(face.physicalRoutes.empty()) {
                        issues.push_back(scope.second+": native face has no physical source");continue;
                    }
                }
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
    out<<",\"face_policy\":"<<q(typedTopology?kTopologyPolicy:kPolicy);
    if(typedTopology) {out<<",\"companion_layers\":";strings(out,preparedCompanions);}
    out<<",\"linear_routes\":";strings(out,selectedLines);
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
            <<"],\"sampling_tolerance_units\":"<<face.samplingToleranceUnits<<",\"display\":[";
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
std::string archiveFaceAnchor(const std::string& route) {
    if(archiveRoutes.empty()) return route;
    const auto found=archiveRoutes.find(route);
    if(found==archiveRoutes.end()) throw std::runtime_error("native face archive anchor absent");
    return found->second;
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
