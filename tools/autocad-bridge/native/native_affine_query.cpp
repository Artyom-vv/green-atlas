#include "native_affine_query.h"
#include "direct_query_kernel.h"
#include "brbetrav.h"
#include "bredge.h"
#include "brbftrav.h"
#include "brface.h"
#include "geblok3d.h"
#include "genurb3d.h"
#include "geponc3d.h"
#include "getol.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <iomanip>
#include <limits>
#include <stdexcept>

namespace ga::nativeQuery {
namespace {
constexpr double kWorldTolerance=1e-8;
constexpr unsigned kEdgeBudget=16384;
bool finite(const AcGePoint3d& p) { return std::isfinite(p.x)&&std::isfinite(p.y)&&std::isfinite(p.z); }
void require(AcBr::ErrorStatus status,const char* operation) {
    if(status!=AcBr::eOk) throw std::runtime_error(std::string(operation)+" status "+std::to_string(int(status)));
}
double nearest(const AcGeCurve3d& curve,const AcGePoint3d& point) {
    AcGeTol tol; tol.setEqualPoint(kWorldTolerance); tol.setEqualVector(kWorldTolerance);
    AcGePointOnCurve3d closest; curve.getClosestPointTo(point,closest,tol);
    AcGeInterval range; curve.getInterval(range);
    if(!std::isfinite(closest.parameter())||!range.contains(closest.parameter())||!finite(closest.point()))
        throw std::runtime_error("invalid native closest-point answer");
    return point.distanceTo(closest.point());
}
struct NativeAffine : AffineEvidence {
    ga::direct::Prepared local;
    AcGeMatrix3d inverse;
    AcGeMatrix3d worldTransform;
    double amplification=0;
    bool distancesReady=false;
    std::vector<std::unique_ptr<AcGeNurbCurve3d>> curves;
    void prepare(AcDbEntity& entity,const AcGeMatrix3d& transform,bool deferDistances) {
        double normSquared=0;
        for(int i=0;i<4;++i) for(int j=0;j<4;++j) {
            if(!std::isfinite(transform(i,j))) throw std::runtime_error("nonfinite transform");
            if(i<3&&j<3) normSquared+=transform(i,j)*transform(i,j);
        }
        amplification=std::sqrt(normSquared); // Conservative norm, not a distance scale.
        worldTransform=transform;
        if(!transform.isUniScaledOrtho() && std::abs(transform.det())<1e-15)
            throw std::runtime_error("singular transform");
        inverse=transform.inverse();
        auto* region=ga::direct::prepareLocalArea(&entity,local);
        regionAreaStatus=int(region->getArea(regionAreaValue));
        // A scalar REGION query may fail on a multi-face body. Query each
        // native face explicitly; do not discard valid membership/edge data.
        AcBrBrepFaceTraverser faceIt; require(faceIt.setBrep(local.local),"native faces");
        for(;!faceIt.done();require(faceIt.next(),"next native face")) {
            if(++faces>kEdgeBudget) throw std::runtime_error("native face budget");
            AcBrFace face; require(faceIt.getFace(face),"native face");
            double area=0; require(face.getArea(area),"native face area");
            if(!std::isfinite(area)||area<0) throw std::runtime_error("invalid native face area");
            localArea+=area;
        }
        const AcGeVector3d x(transform(0,0),transform(1,0),transform(2,0)),
                           y(transform(0,1),transform(1,1),transform(2,1));
        jacobian=x.crossProduct(y).length();
        AcGeBoundBlock3d bounds; require(local.local.getBoundBlock(bounds),"local bound");
        bounds.setToBox(Adesk::kTrue); bounds.getMinMaxPoints(low,high);
        if(!finite(low)||!finite(high)||std::abs(high.z-low.z)>1e-7
            ||std::abs(transform(2,0))+std::abs(transform(2,1))>1e-9)
            throw std::runtime_error("not horizontal in planar affine experiment");
        auto planePoint=low; planePoint.transformBy(transform); worldZ=planePoint.z;
        if(!deferDistances) prepareDistances();
    }
    void prepareDistances() {
        if(distancesReady) return;
        // A failed native fit cannot leave a partial distance oracle reusable.
        curves.clear(); straightEdges.clear(); edgeWitnesses.clear();
        maxFit=0; maxWitnessDistance=0; edgeCount=0; allStraight=true;
        const auto& transform=worldTransform;
        const double requested=kWorldTolerance/std::max(1.0,amplification);
        AcBrBrepEdgeTraverser it; require(it.setBrep(local.local),"local edge traversal");
        for(;!it.done();require(it.next(),"next edge")) {
            if(curves.size()>=kEdgeBudget) throw std::runtime_error("native edge budget");
            AcBrEdge edge; require(it.getEdge(edge),"local edge");
            auto curve=std::make_unique<AcGeNurbCurve3d>();
            double achieved=-1;
            require(edge.getCurveAsNurb(*curve,&requested,&achieved),"native edge NURB");
            if(!std::isfinite(achieved)||achieved<0||achieved>requested)
                throw std::runtime_error("native NURB fit tolerance not achieved/reported");
            maxFit=std::max(maxFit,achieved*amplification);
            curve->transformBy(transform);
            // Independent source-curve points validate transport/trim, not an exported polygon.
            AcGeCurve3d* raw=nullptr; require(edge.getCurve(raw),"local source curve");
            std::unique_ptr<AcGeCurve3d> source(raw);
            if(!source) throw std::runtime_error("null local source curve");
            AcGeInterval interval; source->getInterval(interval);
            if(!interval.isBounded()||!std::isfinite(interval.lowerBound())||!std::isfinite(interval.upperBound()))
                throw std::runtime_error("unbounded local source curve");
            AcGe::EntityId curveType;
            require(edge.getCurveType(curveType),"native curve type");
            const bool straight=curveType==AcGe::kLineSeg3d||curveType==AcGe::kLine3d;
            allStraight=allStraight&&straight;
            if(straight) {
                auto a=source->evalPoint(interval.lowerBound()),b=source->evalPoint(interval.upperBound());
                a.transformBy(transform); b.transformBy(transform);
                if(a.distanceTo(b)<=1e-12) throw std::runtime_error("degenerate straight-edge oracle");
                straightEdges.emplace_back(a,b);
            }
            for(int i=0;i<5;++i) {
                auto point=source->evalPoint(interval.lowerBound()+(interval.upperBound()-interval.lowerBound())*i/4.0);
                point.transformBy(transform);
                if(!finite(point)) throw std::runtime_error("nonfinite edge witness");
                maxWitnessDistance=std::max(maxWitnessDistance,nearest(*curve,point));
                if(i==2) edgeWitnesses.push_back(point);
            }
            curves.push_back(std::move(curve));
        }
        if(curves.empty()) throw std::runtime_error("no native edges");
        edgeCount=curves.size();
        distancesReady=true;
    }
    ga::direct::Answer query(const AcGePoint3d& world) const {
        auto localPoint=world; localPoint.transformBy(inverse);
        auto answer=ga::direct::membership(local.local,localPoint);
        answer.distance=std::numeric_limits<double>::infinity();
        for(const auto& curve:curves) answer.distance=std::min(answer.distance,nearest(*curve,world));
        answer.distanceComplete=std::isfinite(answer.distance);
        return answer;
    }
    double analyticDistance(const AcGePoint3d& point) const {
        double best=std::numeric_limits<double>::infinity();
        for(const auto& [a,b]:straightEdges) {
            const auto v=b-a,w=point-a;
            const double fraction=std::clamp(w.dotProduct(v)/v.lengthSqrd(),0.0,1.0);
            best=std::min(best,(w-v*fraction).length());
        }
        return best;
    }
};
}
struct AffineAreaQuery::Impl { NativeAffine native; bool ready=false; };
AffineAreaQuery::AffineAreaQuery():impl(std::make_unique<Impl>()) {}
AffineAreaQuery::~AffineAreaQuery()=default;
void AffineAreaQuery::prepare(AcDbEntity& entity,const AcGeMatrix3d& transform,bool deferDistances) {
    // A failed re-prepare cannot leave old geometry available under a new identity.
    impl=std::make_unique<Impl>();
    impl->native.prepare(entity,transform,deferDistances);
    impl->ready=true;
}
ga::direct::Answer AffineAreaQuery::query(const AcGePoint3d& point) const {
    if(!impl->ready||!finite(point)) throw std::runtime_error("native area not ready or nonfinite point");
    impl->native.prepareDistances();
    return impl->native.query(point);
}
ga::direct::Answer AffineAreaQuery::queryPlanar(const AcGePoint3d& point) const {
    if(!finite(point)) throw std::runtime_error("nonfinite planar query point");
    return query({point.x,point.y,impl->native.worldZ});
}
ga::direct::Answer AffineAreaQuery::membershipPlanar(const AcGePoint3d& point) const {
    if(!impl->ready||!finite(point)) throw std::runtime_error("native area not ready or nonfinite point");
    AcGePoint3d localPoint(point.x,point.y,impl->native.worldZ);
    localPoint.transformBy(impl->native.inverse);
    return ga::direct::membership(impl->native.local.local,localPoint);
}
const AffineEvidence& AffineAreaQuery::evidence() const {
    if(!impl->ready) throw std::runtime_error("native area not ready");
    return impl->native;
}
double AffineAreaQuery::analyticDistance(const AcGePoint3d& point) const {
    if(!impl->ready||!finite(point)) throw std::runtime_error("native area not ready or nonfinite point");
    impl->native.prepareDistances();
    return impl->native.analyticDistance(point);
}
}
