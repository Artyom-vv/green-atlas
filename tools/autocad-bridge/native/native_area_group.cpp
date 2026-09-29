#include "native_area_group.h"
#include "AcString.h"
#include "dbcurve.h"
#include <cmath>
#include <limits>
#include <set>
#include <stdexcept>

namespace ga::nativeQuery {
namespace {
void require(Acad::ErrorStatus status, const char* op) {
    if(status != Acad::eOk)
        throw std::runtime_error(std::string(op)+" status "+std::to_string(int(status)));
}
bool finite(const AcGePoint3d& p) {
    return std::isfinite(p.x)&&std::isfinite(p.y)&&std::isfinite(p.z);
}
double verifyCycle(const std::vector<AcDbEntity*>& input) {
    if(input.empty()||input.size()>kAreaGroupMemberLimit)
        throw std::runtime_error("native area group member limit");
    std::vector<AcGePoint3d> ends;
    std::set<AcDbEntity*> unique;
    double length=0;
    for(auto* entity:input) {
        if(!entity||!unique.insert(entity).second)
            throw std::runtime_error("null or duplicate area group member");
        const std::string type=AcString(entity->isA()->name()).utf8Str();
        auto* curve=AcDbCurve::cast(entity);
        if(!curve||(type!="AcDbLine"&&type!="AcDbArc"&&type!="AcDbPolyline"
            &&type!="AcDbCircle"&&type!="AcDbEllipse"&&type!="AcDbSpline"))
            throw std::runtime_error("unsupported native group curve");
        if(!entity->objectId().isNull()&&entity->isWriteEnabled())
            throw std::runtime_error("database group curve must be read-only");
        AcGePoint3d a,b;
        require(curve->getStartPoint(a),"group start"); require(curve->getEndPoint(b),"group end");
        if(!finite(a)||!finite(b)) throw std::runtime_error("nonfinite group endpoints");
        ends.push_back(a); ends.push_back(b);
        double start=0,end=0,distStart=0,distEnd=0;
        require(curve->getStartParam(start),"group start parameter");
        require(curve->getEndParam(end),"group end parameter");
        require(curve->getDistAtParam(start,distStart),"group start distance");
        require(curve->getDistAtParam(end,distEnd),"group end distance");
        const double piece=std::abs(distEnd-distStart);
        if(!std::isfinite(piece)||piece<=kNativeJoinTolerance)
            throw std::runtime_error("degenerate group curve");
        length+=piece;
    }
    std::vector<std::size_t> partner(ends.size());
    for(std::size_t i=0;i<ends.size();++i) {
        unsigned matches=0;
        for(std::size_t j=0;j<ends.size();++j)
            if(i!=j&&ends[i].distanceTo(ends[j])<=kNativeJoinTolerance) {partner[i]=j;++matches;}
        if(matches!=1) throw std::runtime_error("group has an open or ambiguous junction");
    }
    std::set<std::size_t> visited;
    std::size_t next=0;
    do {
        if(!visited.insert(next/2).second) throw std::runtime_error("group repeats a curve");
        next=partner[next^1];
    } while(next!=0);
    if(visited.size()!=input.size()) throw std::runtime_error("group contains disconnected cycles");
    return length;
}
}
struct AreaGroupQuery::Impl {
    // BRep query must be destroyed before the transient native REGION.
    std::unique_ptr<AcDbRegion> region;
    std::unique_ptr<AffineAreaQuery> query;
    AcGeMatrix3d transform;
};
AreaGroupQuery::AreaGroupQuery():impl(std::make_unique<Impl>()) {}
AreaGroupQuery::~AreaGroupQuery()=default;
void AreaGroupQuery::prepare(const std::vector<AcDbEntity*>& curves,const AcGeMatrix3d& transform,bool deferDistances) {
    impl=std::make_unique<Impl>();
    const double sourceLength=verifyCycle(curves);
    AcArray<AcDbEntity*> inputs; for(auto* curve:curves) inputs.append(curve);
    AcArray<AcDbRegion*> regions;
    const auto status=AcDbRegion::createFromCurves(inputs,regions);
    if(status!=Acad::eOk||regions.length()!=1) {
        const auto count=regions.length();
        for(auto* region:regions) delete region;
        throw std::runtime_error("native group createFromCurves status "+std::to_string(int(status))
            +", regions "+std::to_string(count));
    }
    impl->region.reset(regions[0]);
    double perimeter=0;
    require(impl->region->getPerimeter(perimeter),"group perimeter");
    const double tolerance=kNativeJoinTolerance*curves.size()
        +64*std::numeric_limits<double>::epsilon()*sourceLength;
    if(!std::isfinite(perimeter)||std::abs(perimeter-sourceLength)>tolerance)
        throw std::runtime_error("native group boundary does not cover all input lengths");
    auto query=std::make_unique<AffineAreaQuery>(); query->prepare(*impl->region,transform,deferDistances);
    if(query->evidence().faces!=1||query->evidence().localArea<=0)
        throw std::runtime_error("native group did not produce one nonempty face");
    impl->query=std::move(query);
    impl->transform=transform;
}
ga::direct::Answer AreaGroupQuery::queryPlanar(const AcGePoint3d& p) const {
    if(!impl->query) throw std::runtime_error("native area group not ready");
    return impl->query->queryPlanar(p);
}
ga::direct::Answer AreaGroupQuery::membershipPlanar(const AcGePoint3d& p) const {
    if(!impl->query) throw std::runtime_error("native area group not ready");
    return impl->query->membershipPlanar(p);
}
const AffineEvidence& AreaGroupQuery::evidence() const {
    if(!impl->query) throw std::runtime_error("native area group not ready");
    return impl->query->evidence();
}
std::unique_ptr<AcDbRegion> AreaGroupQuery::copyWorldRegion() const {
    if(!impl->query) throw std::runtime_error("native area group not ready");
    AcDbEntity* raw=nullptr;
    const auto status=impl->region->getTransformedCopy(impl->transform,raw);
    std::unique_ptr<AcDbEntity> copy(raw);
    require(status,"group world REGION copy");
    auto* region=AcDbRegion::cast(copy.get());
    if(!region) throw std::runtime_error("group world REGION unavailable");
    copy.release();return std::unique_ptr<AcDbRegion>(region);
}
}
