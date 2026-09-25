// Research-only all-object predicate. Layers/clearances are explicit fixture
// assumptions, not an automatic classifier or a normative planting result.
#include "xref_window_queries.h"
#include "native_affine_query.h"
#include "native_curve_query.h"
#include "native_area_candidates.h"
#include "native_area_group.h"
#include "xref_instance_access.h"
#include "file_io.h"
#include "AcString.h"
#include "dbcurve.h"
#include "dbhatch.h"
#include "dbregion.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <iomanip>
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <vector>

namespace ga::xref {
namespace {
constexpr unsigned kObjectBudget=20000,kPointBudget=10000;
constexpr double kTolerance=1e-8;
std::string q(const std::string& s) { return "\""+ga::bridge::jsonEscape(s)+"\""; }
std::string line(std::istream& in) { std::string s; if(!std::getline(in,s)) throw std::runtime_error("incomplete window query request"); return s; }
bool annotation(const std::string& type) {
    static const std::set<std::string> types{"AcDbText","AcDbMText","AcDbAttributeDefinition","AcDbAttribute",
        "AcDbMLeader","AcDbAlignedDimension","AcDbRadialDimension","AcDbRotatedDimension","AcDbLeader"};
    return types.count(type);
}
bool finite(const AcGePoint3d& p) { return std::isfinite(p.x)&&std::isfinite(p.y)&&std::isfinite(p.z); }
struct Rule { std::string role; double clearance=0; };
struct Object {
    ga::direct::ReadEntities opened; // Own a read-open reference for native BRep lifetime.
    std::vector<std::unique_ptr<ga::xref::Instance>> groupedInstances;
    std::vector<std::string> groupRoutes;
    std::string route,layer,type,error;
    Rule rule;
    AcGePoint3d lo,hi;
    std::unique_ptr<ga::nativeQuery::AffineAreaQuery> area;
    std::unique_ptr<ga::nativeQuery::CurveQuery> curve;
    std::unique_ptr<ga::nativeQuery::AreaGroupQuery> group;
    bool intersects(const AcGePoint3d& p) const {
        return p.x>=lo.x-rule.clearance&&p.x<=hi.x+rule.clearance
            &&p.y>=lo.y-rule.clearance&&p.y<=hi.y+rule.clearance;
    }
};
struct Verdict {
    std::string result="draft",reason="known_checks_passed",blocker;
    unsigned unknown=0,nativeQueries=0;
    std::vector<std::string> queryFailures;
    std::vector<std::string> unknownRoutes;
    double nearestBlockedDistance=std::numeric_limits<double>::infinity(),requiredClearance=0;
    bool operator==(const Verdict& b) const {
        return result==b.result&&reason==b.reason&&blocker==b.blocker&&unknown==b.unknown
            &&nativeQueries==b.nativeQueries&&queryFailures==b.queryFailures;
    }
};
void emit(std::ostream& out,const AcGePoint3d& point,const Verdict& v,bool evidence=false) {
    out<<"{\"point\":["<<point.x<<','<<point.y<<','<<point.z<<"],\"result\":"<<q(v.result)
        <<",\"reason\":"<<q(v.reason)<<",\"blocker\":"<<q(v.blocker)
        <<",\"local_unknown\":"<<v.unknown<<",\"native_queries\":"<<v.nativeQueries<<",\"query_failures\":[";
    for(std::size_t i=0;i<v.queryFailures.size();++i) out<<(i?",":"")<<q(v.queryFailures[i]);
    out<<']';
    if(evidence&&v.result=="blocked"&&std::isfinite(v.nearestBlockedDistance))
        out<<",\"nearest_blocked_distance\":"<<v.nearestBlockedDistance
            <<",\"actual_distance\":"<<v.nearestBlockedDistance
            <<",\"required_clearance\":"<<v.requiredClearance;
    if(evidence) {
        out<<",\"unknown_routes\":[";
        for(std::size_t i=0;i<v.unknownRoutes.size();++i) out<<(i?",":"")<<q(v.unknownRoutes[i]);
        out<<']';
    }
    out<<'}';
}
}
struct WindowQueries::Impl {
    double x0=0,y0=0,x1=0,y1=0,step=0,spacing=0;
    std::string siteRoute;
    std::map<std::string,Rule> rules;
    std::map<std::string,unsigned> counts;
    std::vector<std::unique_ptr<Object>> objects;
    std::vector<AcGePoint3d> controls;
    bool explicitPoints=false;
    std::vector<AcGePoint3d> points;
    unsigned context=0,areaReady=0,curveReady=0,partial=0,unknownRole=0;
    ga::nativeQuery::AreaCandidates areaCandidates;
    std::map<std::string, std::string> candidateLayers;
    bool groupsFinished=false;
    unsigned acceptedGroups=0;
    std::map<std::string,std::string> rejectedGroups;
    void finishGroups() {
        if(groupsFinished||!explicitPoints) return;
        groupsFinished=true;
        std::set<std::string> replaced;
        for(const auto& candidate:areaCandidates.collect()) {
            if(!candidate.error.empty()) continue;
            const auto& first=candidate.routes.front();
            const auto layer=candidateLayers.at(first);
            const auto rule=rules.at(layer);
            auto item=std::make_unique<Object>(); auto& object=*item;
            object.route=first;object.layer=layer;object.rule=rule;object.type="NativeCurveGroup";
            try {
                std::vector<AcDbEntity*> curves;
                for(const auto& route:candidate.routes) {
                    if(candidateLayers.at(route)!=layer) throw std::runtime_error("group effective layer mismatch");
                    auto member=std::make_unique<ga::xref::Instance>();
                    ga::xref::resolve(*acdbHostApplicationServices()->workingDatabase(),route,*member);
                    if(!curves.empty()&&(curves[0]->ownerId()!=member->entity->ownerId()
                        ||curves[0]->layerId()!=member->entity->layerId()))
                        throw std::runtime_error("group native definition/layer mismatch");
                    curves.push_back(member->entity);object.groupedInstances.push_back(std::move(member));
                }
                const auto& transform=object.groupedInstances.front()->transform;
                object.group=std::make_unique<ga::nativeQuery::AreaGroupQuery>();
                object.group->prepare(curves,transform);
                const auto& evidence=object.group->evidence();
                AcDbExtents extents(evidence.low,evidence.high);extents.transformBy(transform);
                object.lo=extents.minPoint();object.hi=extents.maxPoint();
                if(object.hi.x<x0-rule.clearance||object.lo.x>x1+rule.clearance
                    ||object.hi.y<y0-rule.clearance||object.lo.y>y1+rule.clearance) continue;
                if(objects.size()>=kObjectBudget) throw std::runtime_error("window native object budget");
                object.groupRoutes=candidate.routes;
                replaced.insert(candidate.routes.begin(),candidate.routes.end());
                objects.push_back(std::move(item));++acceptedGroups;
            } catch(const std::exception& error) {
                // Failed discovery never erases the original curve checks.
                rejectedGroups.emplace(first,error.what());
            }
        }
        objects.erase(std::remove_if(objects.begin(),objects.end(),[&](const auto& object) {
            return !object->group&&replaced.count(object->route);
        }),objects.end());
        // A pair becomes one obstacle only AFTER native area validation.
        counts.clear();areaReady=curveReady=partial=unknownRole=0;
        for(const auto& object:objects) {
            ++counts[object->layer+":"+object->rule.role];
            areaReady+=bool(object->area||object->group);curveReady+=bool(object->curve);
            partial+=!object->error.empty();unknownRole+=object->rule.role=="unknown";
        }
    }
    Verdict evaluate(const AcGePoint3d& point,bool unculled=false) const {
        Verdict v; bool siteFound=false;
        const auto unknown=[&](const std::string& route) {
            ++v.unknown;
            if(explicitPoints&&!route.empty()&&(v.unknownRoutes.empty()||v.unknownRoutes.back()!=route))
                v.unknownRoutes.push_back(route);
        };
        const auto block=[&](const Object& object,const std::string& reason,double distance) {
            const bool measured=std::isfinite(distance)&&distance>=0;
            // Explicit batches identify the nearest known blocking witness.
            // Legacy evaluate keeps its original first-blocker semantics.
            if(v.result!="blocked"||(explicitPoints&&measured&&distance<v.nearestBlockedDistance)) {
                v.result="blocked";v.blocker=object.route;v.reason=reason;
                v.nearestBlockedDistance=measured?distance:std::numeric_limits<double>::infinity();
                v.requiredClearance=object.rule.clearance;
            }
        };
        for(const auto& item:objects) {
            const auto& object=*item;
            const bool site=object.rule.role=="site";
            if(site) siteFound=true;
            const bool near=object.intersects(point);
            if(!site&&!near&&!(unculled&&(object.area||object.curve||object.group))) continue;
            if(object.rule.role=="unknown") { unknown(object.route); continue; }
            if(!object.error.empty()&&(near||site)) unknown(object.route);
            if(object.area||object.group) {
                ++v.nativeQueries;
                try {
                    const auto a=object.group?object.group->queryPlanar(point):object.area->queryPlanar(point);
                    if(a.status||a.membership=="unknown"||!a.distanceComplete) {
                        unknown(object.route);
                        v.queryFailures.push_back(object.route+":area_status="+std::to_string(a.status)
                            +",membership="+a.membership+",distance_complete="+std::to_string(a.distanceComplete));
                    }
                    else {
                        const bool occupied=a.membership!="outside";
                        const bool blocked=site?!occupied:occupied;
                        if(blocked||a.distance<object.rule.clearance-kTolerance) {
                            block(object,blocked?(site?"outside_site":"native_occupied"):"native_clearance",a.distance);
                        }
                    }
                } catch(const std::exception& e) { unknown(object.route);v.queryFailures.push_back(object.route+":area:"+e.what()); }
            }
            if(object.curve) {
                ++v.nativeQueries;
                const auto answer=object.curve->queryPlanar(point);
                if(answer.status!=0||!answer.distanceComplete) {
                    unknown(object.route);v.queryFailures.push_back(object.route+":curve_status="+std::to_string(answer.status));
                }
                else if(answer.distance<object.rule.clearance-kTolerance) {
                    block(object,"native_curve_clearance",answer.distance);
                }
            }
        }
        if(!siteFound) { unknown(siteRoute); if(v.result!="blocked") { v.result="unknown"; v.reason="site_not_found"; } }
        if(v.result=="draft"&&v.unknown) {
            v.reason="known_checks_passed_with_local_unknown";
            if(explicitPoints) { v.result="unknown";v.reason="local_native_or_role_unknown"; }
        }
        // Legacy nonblocked points remain DRAFT. Explicit batches instead keep
        // local unknown as UNKNOWN; even a clear local check is still only DRAFT
        // because unlocated sources/XREF and experimental rules are not certified.
        return v;
    }
};
WindowQueries::WindowQueries():impl(std::make_unique<Impl>()) {}
WindowQueries::~WindowQueries()=default;
const std::string& WindowQueries::pinnedSiteRoute() const {
    static const std::string none;
    return impl->explicitPoints?impl->siteRoute:none;
}
void WindowQueries::collectAreaCandidate(AcDbEntity& entity,const std::string& route,const std::string& layer) {
    auto& s=*impl;
    if(!s.explicitPoints||route==s.siteRoute||!s.rules.count(layer)||s.rules.at(layer).role!="area") return;
    s.areaCandidates.consider(entity,route);
    s.candidateLayers.emplace(route,layer);
}
void WindowQueries::read(std::istream& in,double x0,double y0,double x1,double y1,double padding,bool explicitPoints) {
    auto& s=*impl; s.x0=x0;s.y0=y0;s.x1=x1;s.y1=y1;s.explicitPoints=explicitPoints;
    if(explicitPoints) {
        long long pointCount=-1;std::istringstream count(line(in));count>>pointCount;
        if(!count||pointCount<0||pointCount>kPointBudget||!(count>>std::ws).eof())
            throw std::runtime_error("bounded explicit point count required");
        s.points.reserve(static_cast<std::size_t>(pointCount));
        for(long long i=0;i<pointCount;++i) {
            AcGePoint3d p;std::istringstream row(line(in));row>>p.x>>p.y>>p.z;
            if(!row||!finite(p)||!(row>>std::ws).eof()) throw std::runtime_error("invalid explicit point");
            // A point outside the inventoried window could silently miss an
            // obstacle. Caller must choose a window covering the entire batch.
            if(p.x<x0||p.x>x1||p.y<y0||p.y>y1) throw std::runtime_error("explicit point outside inventory window");
            s.points.push_back(p); // Preserve order, duplicates and input Z.
        }
    } else {
        std::istringstream numbers(line(in)); numbers>>s.step>>s.spacing;
        if(!numbers||!std::isfinite(s.step)||!std::isfinite(s.spacing)||s.step<=0||s.spacing<=0
            ||((x1-x0)/s.step+1)*((y1-y0)/s.step+1)>kPointBudget) throw std::runtime_error("bounded query grid required");
    }
    s.siteRoute=line(in);
    const int n=std::stoi(line(in)); if(n<0||n>1000) throw std::runtime_error("bounded explicit rules required");
    for(int i=0;i<n;++i) {
        const auto layer=line(in); Rule rule;
        std::istringstream policy(line(in)); policy>>rule.role>>rule.clearance;
        if(!policy||!std::isfinite(rule.clearance)||rule.clearance<0||rule.clearance>padding
            ||(rule.role!="area"&&rule.role!="curve"&&rule.role!="context"&&rule.role!="unknown")
            ||s.rules.count(layer)) throw std::runtime_error("invalid explicit layer rule");
        s.rules.emplace(layer,rule);
    }
    if(explicitPoints) return; // No controls/grid/selection/repeat contract in this mode.
    const int nControls=std::stoi(line(in)); if(nControls<0||nControls>64) throw std::runtime_error("bounded controls required");
    for(int i=0;i<nControls;++i) {
        AcGePoint3d p; std::istringstream row(line(in)); row>>p.x>>p.y>>p.z;
        if(!row||!finite(p)) throw std::runtime_error("invalid control");
        s.controls.push_back(p); // Only independent/previous known BLOCKED points, never invented positive safety.
    }
}
void WindowQueries::inspect(AcDbEntity& entity,const AcGeMatrix3d& transform,
                           const std::string& route,const std::string& layer,
                           const AcGePoint3d& lo,const AcGePoint3d& hi) {
    auto& s=*impl;
    const std::string type=AcString(entity.isA()->name()).utf8Str();
    Rule rule=s.rules.count(layer)?s.rules.at(layer):Rule{"unknown",0};
    if(route==s.siteRoute) rule={"site",0};
    if(annotation(type)||rule.role=="context") { ++s.context; return; }
    if(s.objects.size()>=kObjectBudget) throw std::runtime_error("window native object budget");
    auto item=std::make_unique<Object>(); auto& o=*item;
    o.route=route; o.layer=layer; o.type=type; o.rule=rule;o.lo=lo;o.hi=hi;
    ++s.counts[layer+":"+rule.role];
    if(rule.role=="unknown") ++s.unknownRole;
    else {
        AcDbEntity* retained=nullptr;
        const auto status=acdbOpenObject(retained,entity.objectId(),AcDb::kForRead);
        if(status!=Acad::eOk||!retained) o.error="retain entity status "+std::to_string(int(status));
        else {
            o.opened.values.push_back(retained);
            auto* curve=AcDbCurve::cast(retained);
            const bool area=rule.role=="site"||rule.role=="area"||AcDbHatch::cast(retained)||AcDbRegion::cast(retained)||(curve&&curve->isClosed());
            if(area) try {
                auto prepared=std::make_unique<ga::nativeQuery::AffineAreaQuery>(); prepared->prepare(*retained,transform);
                o.area=std::move(prepared); ++s.areaReady;
            } catch(const std::exception& e) { o.error=e.what(); }
            if(!o.area&&curve) {
                try {
                    auto prepared=std::make_unique<ga::nativeQuery::CurveQuery>();
                    prepared->prepare(*retained,transform);
                    o.curve=std::move(prepared); ++s.curveReady;
                } catch(const std::exception& e) { o.error+="; "+std::string(e.what()); }
            }
            if(!o.area&&!o.curve&&o.error.empty()) o.error="no native area/curve capability in this experiment";
        }
        if(!o.error.empty()) ++s.partial;
    }
    s.objects.push_back(std::move(item));
}
void WindowQueries::write(std::ostream& out) {
    impl->finishGroups();
    const auto& s=*impl;
    const auto started=std::chrono::steady_clock::now();
    out<<"{\"scope\":"<<q(s.explicitPoints
        ?"explicit points against all broad-phase objects; experimental roles, DRAFT is not certified safety"
        :"all broad-phase objects with explicit experimental roles; DRAFT only, not certified safety")<<",\"context\":"<<s.context
        <<",\"objects\":"<<s.objects.size()<<",\"native_area_ready\":"<<s.areaReady<<",\"native_curve_ready\":"<<s.curveReady
        <<",\"partial_objects\":"<<s.partial<<",\"unknown_role_objects\":"<<s.unknownRole
        <<",\"accepted_native_groups\":"<<s.acceptedGroups<<",\"rejected_native_groups\":[";
    bool firstRejected=true;
    for(const auto& [route,error]:s.rejectedGroups) {
        out<<(firstRejected?"":",")<<"{\"route\":"<<q(route)<<",\"error\":"<<q(error)<<'}';firstRejected=false;
    }
    out<<"],\"object_ledger\":[";
    for(std::size_t i=0;i<s.objects.size();++i) {
        const auto& o=*s.objects[i];
        out<<(i?",":"")<<"{\"route\":"<<q(o.route)<<",\"layer\":"<<q(o.layer)<<",\"type\":"<<q(o.type)
            <<",\"role\":"<<q(o.rule.role)<<",\"clearance\":"<<o.rule.clearance<<",\"area_ready\":"<<((o.area||o.group)?"true":"false")
            <<",\"curve_ready\":"<<(o.curve?"true":"false")<<",\"error\":"<<q(o.error);
        out<<",\"native_group_members\":[";
        for(std::size_t j=0;j<o.groupRoutes.size();++j) out<<(j?",":"")<<q(o.groupRoutes[j]);
        out<<']';
        if(s.explicitPoints) out<<",\"bounds\":[["<<o.lo.x<<','<<o.lo.y<<','<<o.lo.z
            <<"],["<<o.hi.x<<','<<o.hi.y<<','<<o.hi.z<<"]]";
        out<<'}';
    }
    out<<"],\"answers\":[";
    if(s.explicitPoints) {
        for(std::size_t i=0;i<s.points.size();++i) {
            out<<(i?",":"");emit(out,s.points[i],s.evaluate(s.points[i]),true);
        }
        out<<"],\"elapsed_ms\":"<<std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count()<<'}';
        return;
    }
    std::vector<std::pair<AcGePoint3d,Verdict>> first;
    std::vector<AcGePoint3d> selected;
    std::map<std::string,unsigned> decisions,queryFailures;
    unsigned pointCount=0,queryCount=0,repeatMismatches=0,manualMismatches=0,heightMismatches=0;
    for(unsigned iy=0;s.y0+iy*s.step<=s.y1+kTolerance;++iy) for(unsigned ix=0;s.x0+ix*s.step<=s.x1+kTolerance;++ix) {
        if(pointCount>=kPointBudget) throw std::runtime_error("grid budget");
        AcGePoint3d p(s.x0+ix*s.step,s.y0+iy*s.step,0);
        const auto v=s.evaluate(p); first.emplace_back(p,v);++decisions[v.result];queryCount+=v.nativeQueries;
        for(const auto& failure:v.queryFailures) ++queryFailures[failure];
        out<<(pointCount++?",":"");emit(out,p,v);
        if(v.result=="draft"&&std::none_of(selected.begin(),selected.end(),[&](const auto& a){return std::hypot(a.x-p.x,a.y-p.y)<s.spacing-kTolerance;})) selected.push_back(p);
    }
    for(int repeat=0;repeat<2;++repeat) for(const auto& [p,v]:first) if(!(s.evaluate(p)==v)) ++repeatMismatches;
    out<<"],\"selected\":[";
    for(std::size_t i=0;i<selected.size();++i) {
        const auto& p=selected[i];const auto manual=s.evaluate(p);
        if(manual.result!="draft") ++manualMismatches;
        auto elevated=p;elevated.z=150;if(!(s.evaluate(elevated)==manual))++heightMismatches;
        out<<(i?",":"");emit(out,p,manual);
    }
    out<<"],\"controls\":[";unsigned failed=0;
    for(std::size_t i=0;i<s.controls.size();++i) {
        const auto v=s.evaluate(s.controls[i]);if(v.result!="blocked")++failed;
        out<<(i?",":"");emit(out,s.controls[i],v);
    }
    // Independent broad-phase check: for fixed controls and spread-out selected
    // candidates, ask EVERY prepared native object, without its point AABB cull.
    std::vector<AcGePoint3d> checkPoints=s.controls;
    for(std::size_t i=0;i<selected.size();i+=std::max<std::size_t>(1,selected.size()/12)) checkPoints.push_back(selected[i]);
    unsigned broadPhaseMismatches=0,broadPhaseQueryFailures=0;
    out<<"],\"unculled_checks\":[";
    for(std::size_t i=0;i<checkPoints.size();++i) {
        const auto& p=checkPoints[i];const auto fast=s.evaluate(p),full=s.evaluate(p,true);
        if(fast.result!=full.result||fast.reason!=full.reason||fast.blocker!=full.blocker) ++broadPhaseMismatches;
        broadPhaseQueryFailures+=full.queryFailures.size();
        out<<(i?",":"")<<"{\"culled\":";emit(out,p,fast);out<<",\"all_prepared\":";emit(out,p,full);out<<'}';
    }
    out<<"],\"broad_phase_mismatches\":"<<broadPhaseMismatches<<",\"unculled_query_failures\":"<<broadPhaseQueryFailures
        <<",\"native_query_failures_first_pass\":{";
    bool firstFailure=true;
    for(const auto& [failure,count]:queryFailures) { out<<(firstFailure?"":",")<<q(failure)<<':'<<count;firstFailure=false; }
    out<<"},\"query_points\":"<<pointCount<<",\"native_queries_first_pass\":"<<queryCount
        <<",\"repeats\":3,\"repeat_mismatches\":"<<repeatMismatches<<",\"manual_recheck_mismatches\":"<<manualMismatches
        <<",\"height_query_mismatches\":"<<heightMismatches<<",\"controls_failed\":"<<failed
        <<",\"selected_count\":"<<selected.size()<<",\"blocked_points\":"<<decisions["blocked"]
        <<",\"draft_points\":"<<decisions["draft"]<<",\"unknown_points\":"<<decisions["unknown"]
        <<",\"elapsed_ms\":"<<std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count()<<'}';
}
}
