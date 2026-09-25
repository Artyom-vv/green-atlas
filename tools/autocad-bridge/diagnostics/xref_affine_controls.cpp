#include "xref_affine_controls.h"
#include "native_affine_query.h"
#include "file_io.h"
#include <chrono>
#include <cmath>
#include <iomanip>
#include <sstream>

namespace ga::xref {
namespace {
std::string q(const std::string& s) { return "\""+ga::bridge::jsonEscape(s)+"\""; }
void measure(AcDbEntity& entity,const AcDbObjectIdArray& parents,const AcGeMatrix3d& transform,
             const std::vector<AreaControl>& controls,std::ostream& out) {
    const auto started=std::chrono::steady_clock::now();
    ga::nativeQuery::AffineAreaQuery native; native.prepare(entity,transform);
    const auto& evidence=native.evidence();
    ga::direct::Prepared baseline;
    bool comparable=true; std::string baselineError;
    try { ga::direct::prepareResolved(&entity,parents,transform,baseline); }
    catch(const std::exception& e) { comparable=false; baselineError=e.what(); }
    std::vector<AreaControl> points;
    for(auto c:controls) { c.point.transformBy(transform); points.push_back(c); }
    for(int i=0;i<9;++i) for(int j=0;j<9;++j) {
        AcGePoint3d p(evidence.low.x-1+(evidence.high.x-evidence.low.x+2)*i/8,
                     evidence.low.y-1+(evidence.high.y-evidence.low.y+2)*j/8,evidence.low.z);
        points.push_back({p.transformBy(transform),"discovery","grid"});
    }
    for(const auto& point:evidence.edgeWitnesses) points.push_back({point,"edge","native_local_edge_transformed"});
    auto outside=evidence.high+AcGeVector3d(1,1,0); outside.transformBy(transform);
    points.push_back({outside,"outside","outside_local_bounds_transformed"});
    unsigned unknown=0,passed=0,failed=0,different=0,repeated=0,oracleFailures=0;
    double maxDelta=0,maxRepeat=0,maxOracleDelta=0;
    std::vector<ga::direct::Answer> first;
    out<<"{\"method\":\"native_local_membership_and_world_NURB_edges\",\"native_edges\":"<<evidence.edgeCount
        <<",\"native_faces\":"<<evidence.faces<<",\"region_getArea_status\":"<<evidence.regionAreaStatus<<",\"region_getArea_value\":";
    if(evidence.regionAreaStatus==0&&std::isfinite(evidence.regionAreaValue)) out<<evidence.regionAreaValue; else out<<"null";
    out
        <<",\"native_local_area\":"<<evidence.localArea<<",\"planar_jacobian\":"<<evidence.jacobian
        <<",\"derived_world_area\":"<<evidence.localArea*evidence.jacobian
        <<",\"max_native_fit_world_bound\":"<<evidence.maxFit<<",\"max_edge_witness_distance\":"<<evidence.maxWitnessDistance
        <<",\"baseline_available\":"<<(comparable?"true":"false")<<",\"baseline_error\":"<<q(baselineError)<<",\"answers\":[";
    for(std::size_t i=0;i<points.size();++i) {
        const auto& c=points[i]; const auto a=native.query(c.point); first.push_back(a);
        if(evidence.allStraight) {
            const double delta=std::abs(a.distance-native.analyticDistance(c.point));
            maxOracleDelta=std::max(maxOracleDelta,delta);
            if(!std::isfinite(delta)||delta>1e-7) ++oracleFailures;
        }
        if(a.status!=0||a.membership=="unknown"||!a.distanceComplete) ++unknown;
        if(c.expected!="discovery") {
            if(a.status==0&&a.membership==c.expected&&a.distanceComplete&&(c.expected!="edge"||a.distance<=1e-7)) ++passed;
            else ++failed;
        }
        if(comparable) {
            const auto b=ga::direct::query(baseline,c.point,false,true);
            if(b.status!=a.status||b.membership!=a.membership||b.distanceComplete!=a.distanceComplete) ++different;
            if(a.distanceComplete&&b.distanceComplete) {
                maxDelta=std::max(maxDelta,std::abs(a.distance-b.distance));
                if(std::abs(a.distance-b.distance)>1e-7) ++different;
            }
        }
        out<<(i?",":"")<<"{\"point\":["<<c.point.x<<','<<c.point.y<<','<<c.point.z<<"],\"expected\":"<<q(c.expected)
            <<",\"label\":"<<q(c.label)<<",\"status\":"<<a.status<<",\"membership\":"<<q(a.membership)<<",\"distance\":"<<a.distance<<'}';
    }
    for(int repeat=0;repeat<2;++repeat) for(std::size_t i=0;i<points.size();++i)
        if(!ga::direct::stable(native.query(points[i].point),first[i],maxRepeat)) ++repeated;
    out<<"],\"query_points\":"<<points.size()<<",\"unknown\":"<<unknown<<",\"controls_passed\":"<<passed
        <<",\"controls_failed\":"<<failed<<",\"baseline_mismatches\":"<<different<<",\"max_baseline_distance_delta\":"<<maxDelta
        <<",\"independent_straight_edge_oracle\":"<<(evidence.allStraight?"true":"false")
        <<",\"oracle_failures\":"<<oracleFailures<<",\"max_oracle_delta\":"<<maxOracleDelta
        <<",\"repeat_mismatches\":"<<repeated<<",\"max_repeat_delta\":"<<maxRepeat<<",\"repeats\":3,\"passed\":"
        <<(unknown==0&&failed==0&&passed>0&&different==0&&repeated==0&&oracleFailures==0&&evidence.maxWitnessDistance<=1e-7?"true":"false")
        <<",\"elapsed_ms\":"<<std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count()<<'}';
}
}
void inspectAffineQueries(AcDbEntity& entity,const AcDbObjectIdArray& parents,const AcGeMatrix3d& transform,
                          const std::vector<AreaControl>& controls,std::ostream& out) {
    try { std::ostringstream result; result<<std::setprecision(17); measure(entity,parents,transform,controls,result); out<<result.str(); }
    catch(const std::exception& e) { out<<"{\"passed\":false,\"error\":"<<q(e.what())<<'}'; }
}
}
