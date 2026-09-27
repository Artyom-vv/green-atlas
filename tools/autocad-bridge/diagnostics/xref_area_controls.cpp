// Query actual loaded CAD instances. No point sampling for representation and
// no contour repairs. The small grid is query input, never a geometry export.
#include "xref_area_controls.h"
#include "xref_line_controls.h"
#include "xref_subentity_controls.h"
#include "xref_affine_controls.h"
#include "direct_query_kernel.h"
#include "file_io.h"
#include "AcDbCompoundObjectId.h"
#include "dbpl.h"
#include "dbhatch.h"
#include "AcString.h"
#include "brbftrav.h"
#include "brface.h"
#include "geblok3d.h"
#include "geintrvl.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <iomanip>
#include <stdexcept>

namespace ga::xref {
namespace {
constexpr double kAbsoluteTolerance=1e-8;
constexpr unsigned kAutomaticCaseBudget=48;
constexpr int kGridSteps=9;
std::string q(const std::string& s) { return "\""+ga::bridge::jsonEscape(s)+"\""; }
void xyz(std::ostream& out,const AcGePoint3d& p) { out<<'['<<p.x<<','<<p.y<<','<<p.z<<']'; }
void bounds(const AcBrBrep& brep,AcGePoint3d& low,AcGePoint3d& high) {
    AcGeBoundBlock3d block;
    const auto status=brep.getBoundBlock(block);
    if(status!=AcBr::eOk) throw std::runtime_error("bound status "+std::to_string(int(status)));
    // A rotated bound block's opposite corners are not its world AABB.
    block.setToBox(Adesk::kTrue);
    block.getMinMaxPoints(low,high);
}
double surfaceArea(const AcBrBrep& brep,unsigned& faces) {
    AcBrBrepFaceTraverser it;
    auto status=it.setBrep(brep);
    if(status!=AcBr::eOk) throw std::runtime_error("face traversal status "+std::to_string(int(status)));
    double total=0;
    for(;!it.done();status=it.next()) {
        if(status!=AcBr::eOk||++faces>16384) throw std::runtime_error("face traversal failed/budget");
        AcBrFace face; status=it.getFace(face);
        double area=0;
        if(status==AcBr::eOk) status=face.getArea(area);
        if(status!=AcBr::eOk || !std::isfinite(area)||area<0) throw std::runtime_error("face area unavailable");
        total+=area;
    }
    return total;
}
std::string verdict(const ga::direct::Answer& a) {
    if(a.status!=0 || a.membership=="unknown" || !a.distanceComplete) return "unknown";
    if(a.membership!="outside") return "blocked_inside_or_edge";
    if(a.distance<0.5) return "blocked_clearance"; // Experimental drawing-unit offset.
    return "clear_of_selected_area_only";
}
void measurement(AcDbEntity& entity,const AcDbObjectIdArray& parents,
                 const AcGeMatrix3d& traversal,AcDbDatabase& host,
                 const std::vector<AreaControl>& controls,std::ostream& out) {
    const auto started=std::chrono::steady_clock::now();
    AcDbCompoundObjectId compound;
    auto status=compound.set(entity.objectId(),parents,&host);
    AcGeMatrix3d native;
    if(status==Acad::eOk) status=compound.getTransform(native);
    if(status!=Acad::eOk||compound.status()!=AcDbCompoundObjectId::kValid)
        throw std::runtime_error("compound transform status "+std::to_string(int(status)));
    double matrixDelta=0;
    for(int i=0;i<4;++i) for(int j=0;j<4;++j) matrixDelta=std::max(matrixDelta,std::abs(native(i,j)-traversal(i,j)));
    ga::direct::Prepared prepared;
    ga::direct::prepareResolved(&entity,parents,native,prepared);
    inspectSubentityPaths(prepared,entity,parents,out);
    AcGePoint3d low,high,worldLow,worldHigh;
    bounds(prepared.local,low,high); bounds(prepared.world,worldLow,worldHigh);
    if(std::abs(high.z-low.z)>1e-7||std::abs(worldHigh.z-worldLow.z)>1e-7)
        throw std::runtime_error("area not horizontal in this planar experiment");
    unsigned localFaces=0,worldFaces=0;
    const double localArea=surfaceArea(prepared.local,localFaces),worldArea=surfaceArea(prepared.world,worldFaces);
    const AcGeVector3d x(native(0,0),native(1,0),native(2,0)),y(native(0,1),native(1,1),native(2,1));
    const double expectedArea=localArea*x.crossProduct(y).length();
    const double areaDelta=std::abs(worldArea-expectedArea);
    std::vector<AreaControl> queries;
    for(auto c:controls) { c.point.transformBy(native); queries.push_back(c); }
    const double margin=1;
    for(int i=0;i<kGridSteps;++i) for(int j=0;j<kGridSteps;++j) {
        AcGePoint3d p(low.x-margin+(high.x-low.x+2*margin)*i/(kGridSteps-1),
                     low.y-margin+(high.y-low.y+2*margin)*j/(kGridSteps-1),low.z);
        queries.push_back({p.transformBy(native),"discovery","grid"});
    }
    for(std::size_t i=0;i<std::min<std::size_t>(4,prepared.curves.size());++i) {
        AcGeInterval range; prepared.curves[i]->getInterval(range);
        queries.push_back({prepared.curves[i]->evalPoint((range.lowerBound()+range.upperBound())/2),"edge","native_edge_witness"});
    }
    queries.push_back({{worldHigh.x+1,worldHigh.y+1,worldHigh.z},"outside","outside_native_bounds"});
    std::vector<ga::direct::Answer> first;
    unsigned unknown=0,covariance=0,failed=0,passed=0,repeated=0;
    std::map<std::string,unsigned> decisions;
    double maxRepeatDelta=0;
    const auto inverse=native.inverse();
    out<<",\"method\":"<<q(prepared.method)<<",\"native_edges\":"<<prepared.curves.size()
        <<",\"exact_gelib_edges\":"<<prepared.exactGelibCurves<<",\"matrix_delta\":"<<matrixDelta
        <<",\"local_area\":"<<localArea<<",\"world_area\":"<<worldArea<<",\"expected_world_area\":"<<expectedArea
        <<",\"area_delta\":"<<areaDelta<<",\"local_faces\":"<<localFaces<<",\"world_faces\":"<<worldFaces
        <<",\"local_bounds\":["; xyz(out,low); out<<','; xyz(out,high); out<<"]"
        <<",\"world_bounds\":["; xyz(out,worldLow); out<<','; xyz(out,worldHigh); out<<"]"
        <<",\"answers\":[";
    for(std::size_t i=0;i<queries.size();++i) {
        const auto& c=queries[i];
        auto a=ga::direct::query(prepared,c.point,false,true); first.push_back(a);
        const auto decision=verdict(a); ++decisions[decision]; if(decision=="unknown") ++unknown;
        auto localPoint=c.point; localPoint.transformBy(inverse);
        const auto local=ga::direct::membership(prepared.local,localPoint);
        if(local.status!=a.status || local.membership!=a.membership) ++covariance;
        if(c.expected!="discovery") {
            if(a.status==0 && a.membership==c.expected && a.distanceComplete
                && (c.expected!="edge" || a.distance<=1e-7)) ++passed; else ++failed;
        }
        out<<(i?",":"")<<"{\"point\":"; xyz(out,c.point);
        out<<",\"expected\":"<<q(c.expected)<<",\"label\":"<<q(c.label)
            <<",\"status\":"<<a.status<<",\"membership\":"<<q(a.membership)<<",\"decision\":"<<q(decision)
            <<",\"distance\":";
        if(a.distanceComplete) out<<a.distance; else out<<"null";
        out<<'}';
    }
    for(int repeat=0;repeat<2;++repeat) for(std::size_t i=0;i<queries.size();++i) {
        const auto a=ga::direct::query(prepared,queries[i].point,false,true);
        if(!ga::direct::stable(a,first[i],maxRepeatDelta)) ++repeated;
    }
    const bool ok=unknown==0 && covariance==0 && failed==0 && passed>0 && repeated==0
        && matrixDelta<=kAbsoluteTolerance && std::isfinite(areaDelta)
        && areaDelta<=std::max(1e-7,expectedArea*1e-8);
    out<<"],\"query_points\":"<<queries.size()<<",\"repeats\":3,\"unknown\":"<<unknown
        <<",\"covariance_mismatches\":"<<covariance<<",\"controls_passed\":"<<passed<<",\"controls_failed\":"<<failed
        <<",\"repeat_mismatches\":"<<repeated<<",\"max_repeat_distance_delta\":"<<maxRepeatDelta
        <<",\"passed\":"<<(ok?"true":"false")<<",\"decisions\":{";
    bool firstDecision=true;
    for(const auto& [key,value]:decisions) { out<<(firstDecision?"":",")<<q(key)<<':'<<value; firstDecision=false; }
    out<<"},\"elapsed_ms\":"<<std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count();
}
}
void AreaControls::read(std::istream& in) {
    std::string mode; if(!std::getline(in,mode)||mode.empty()) return;
    if(mode!="areas") throw std::runtime_error("unknown ledger mode");
    enabled=true; std::string count; if(!std::getline(in,count)) throw std::runtime_error("area case count required");
    const int n=std::stoi(count); if(n<0||n>16) throw std::runtime_error("bounded area cases");
    for(int i=0;i<n;++i) {
        std::string route,raw;
        if(!std::getline(in,route)||!std::getline(in,raw)) throw std::runtime_error("area case incomplete");
        const int size=std::stoi(raw); if(size<0||size>64||explicitCases.count(route)) throw std::runtime_error("invalid controls");
        auto& controls=explicitCases[route];
        for(int j=0;j<size;++j) {
            if(!std::getline(in,raw)) throw std::runtime_error("missing control");
            std::istringstream row(raw); AreaControl c;
            if(!(row>>c.point.x>>c.point.y>>c.point.z>>c.expected>>c.label)
                ||!std::isfinite(c.point.x)||!std::isfinite(c.point.y)||!std::isfinite(c.point.z)) throw std::runtime_error("invalid control");
            controls.push_back(c);
        }
    }
}
void AreaControls::inspect(AcDbEntity& entity,const AcDbObjectIdArray& parents,
                          const AcGeMatrix3d& traversal,AcDbDatabase& host,
                          const std::string& route,const std::string& context) {
    if(!enabled) return;
    const auto locked=explicitCases.find(route);
    const bool explicitlyRequested=locked!=explicitCases.end();
    auto* polyline=AcDbPolyline::cast(&entity);
    const bool candidate=AcDbRegion::cast(&entity)||AcDbHatch::cast(&entity)||(polyline&&polyline->isClosed());
    if(!candidate&&!explicitlyRequested) return;
    const auto type=std::string(AcString(entity.isA()->name()).utf8Str()),kind=transformKind(traversal);
    auto& seen=populations[context+":"+type+":"+kind];
    const bool automaticPick=seen++==0 && automatic<kAutomaticCaseBudget;
    if(!explicitlyRequested&&!automaticPick) return;
    if(!explicitlyRequested) ++automatic;
    AcString layer; entity.layer(layer);
    rows<<std::setprecision(17)<<(tested++?",":"")<<"{\"route\":"<<q(route)<<",\"context\":"<<q(context)
        <<",\"type\":"<<q(type)<<",\"layer\":"<<q(layer.utf8Str())<<",\"transform_kind\":"<<q(kind)
        <<",\"explicit_case\":"<<(explicitlyRequested?"true":"false");
    ga::bridge::writeAtomicText(progressPath,"{\"stage\":\"querying\",\"route\":"+q(route)+",\"case\":"+std::to_string(tested)+"}");
    try {
        std::ostringstream result; result<<std::setprecision(17);
        measurement(entity,parents,traversal,host,explicitlyRequested?locked->second:std::vector<AreaControl>{},result);
        rows<<result.str();
    }
    catch(const std::exception& e) { rows<<",\"passed\":false,\"error\":"<<q(e.what()); }
    rows<<",\"affine_probe\":";
    inspectAffineQueries(entity,parents,traversal,explicitlyRequested?locked->second:std::vector<AreaControl>{},rows);
    rows<<'}';
    if(explicitlyRequested) explicitCases.erase(locked);
    ga::bridge::writeAtomicText(progressPath,"{\"stage\":\"case_finished\",\"route\":"+q(route)+",\"case\":"+std::to_string(tested)+"}");
}
void AreaControls::finish(std::ostream& out) {
    out<<"["<<rows.str();
    for(const auto& [route,unused]:explicitCases)
        out<<(tested++?",":"")<<"{\"route\":"<<q(route)<<",\"passed\":false,\"error\":\"explicit instance was not reached\"}";
    out<<']';
}
}
