#include "native_clearance.h"
#include "native_affine_query.h"
#include "AcString.h"
#include "dbents.h"
#include "dbpl.h"
#include <cmath>
#include <stdexcept>

namespace ga::clearance {
namespace {
using Entity = std::unique_ptr<AcDbEntity>;
void require(Acad::ErrorStatus status, const char* op) {
    if(status!=Acad::eOk) throw std::runtime_error(std::string(op)+" status "+std::to_string(int(status)));
}
void check(const Cancel& cancelled) {
    if(cancelled&&cancelled()) throw std::runtime_error("clearance cancelled");
}
void validDistance(double value) {
    if(!std::isfinite(value)||value<0) throw std::runtime_error("invalid clearance distance");
}
Region create(const std::vector<AcDbEntity*>& curves) {
    AcArray<AcDbEntity*> input; for(auto* curve:curves) input.append(curve);
    AcArray<AcDbRegion*> output;
    const auto status=AcDbRegion::createFromCurves(input,output);
    if(status!=Acad::eOk||output.length()!=1) {
        for(auto* item:output) delete item;
        throw std::runtime_error("clearance native region status "+std::to_string(int(status))
            +", count "+std::to_string(output.length()));
    }
    Region result(output[0]); area(*result); return result;
}
void unite(Region& into, Region piece) {
    if(!piece) throw std::runtime_error("null clearance piece");
    if(!into) {into=std::move(piece);return;}
    require(into->booleanOper(AcDb::kBoolUnite,piece.get()),"clearance union");
    area(*into);
}
Region disk(const AcGePoint3d& centre, double radius) {
    AcDbCircle circle(centre,AcGeVector3d::kZAxis,radius);
    return create({&circle});
}
void horizontal(const AcGeVector3d& normal) {
    if(!std::isfinite(normal.x)||!std::isfinite(normal.y)||!std::isfinite(normal.z)
        ||std::hypot(normal.x,normal.y)>kPlanarTolerance
        ||std::abs(std::abs(normal.z)-1)>kPlanarTolerance)
        throw std::runtime_error("clearance requires horizontal geometry");
}
AcGePoint3d flat(AcGePoint3d p) {
    if(!std::isfinite(p.x)||!std::isfinite(p.y)||!std::isfinite(p.z))
        throw std::runtime_error("nonfinite clearance point");
    p.z=0;return p;
}
Region lineBand(AcDbLine& line,double radius) {
    auto a=line.startPoint(),b=line.endPoint();
    if(std::abs(a.z-b.z)>kPlanarTolerance) throw std::runtime_error("nonplanar clearance line");
    a=flat(a);b=flat(b);
    const auto axis=b-a;
    if(axis.length()<=kPlanarTolerance) return disk(a,radius);
    const auto shift=AcGeVector3d(-axis.y,axis.x,0).normal()*radius;
    auto region=polygon({a+shift,b+shift,b-shift,a-shift});
    unite(region,disk(a,radius));unite(region,disk(b,radius));return region;
}
Region circleBand(AcDbCircle& circle,double radius) {
    horizontal(circle.normal());
    const auto centre=flat(circle.center());
    const double r=circle.radius();
    auto result=disk(centre,r+radius);
    if(r>radius) {
        auto hole=disk(centre,r-radius);
        require(result->booleanOper(AcDb::kBoolSubtract,hole.get()),"circle clearance hole");
    }
    return result;
}
Region arcBand(AcDbArc& arc,double radius) {
    horizontal(arc.normal());
    // Near-full arcs are common CAD symbols. A sector with almost coincident
    // radial sides can be rejected by REGION. Split the EXACT native arc into
    // two sectors, instead of closing its gap or accepting a partial REGION.
    const double pi=std::acos(-1.);
    const double sweep=arc.totalAngle();
    if(sweep>pi) {
        const double middle=std::fmod(arc.startAngle()+sweep/2,2*pi);
        AcDbArc first(arc.center(),arc.normal(),arc.radius(),arc.startAngle(),middle);
        AcDbArc second(arc.center(),arc.normal(),arc.radius(),middle,arc.endAngle());
        auto result=arcBand(first,radius);unite(result,arcBand(second,radius));return result;
    }
    const auto centre=flat(arc.center());
    AcGePoint3d a,b;require(arc.getStartPoint(a),"arc start");require(arc.getEndPoint(b),"arc end");
    a=flat(a);b=flat(b);
    // AcDbArc angles are OCS angles. Derive WCS XY angles from native endpoints,
    // reversing for a downward normal; this also preserves reflected blocks.
    if(arc.normal().z<0) std::swap(a,b);
    const double turn=2*std::acos(-1.);
    const auto angle=[&](const AcGePoint3d& p) {
        double value=std::atan2(p.y-centre.y,p.x-centre.x);
        return value<0?value+turn:value;
    };
    const double start=angle(a),end=angle(b);
    const double outer=arc.radius()+radius,inner=arc.radius()-radius;
    AcDbArc outside(centre,AcGeVector3d::kZAxis,outer,start,end);
    AcGePoint3d oa,ob;require(outside.getStartPoint(oa),"offset start");require(outside.getEndPoint(ob),"offset end");
    Region result;
    if(inner>kPlanarTolerance) {
        AcDbArc inside(centre,AcGeVector3d::kZAxis,inner,start,end);
        AcGePoint3d ia,ib;require(inside.getStartPoint(ia),"inner start");require(inside.getEndPoint(ib),"inner end");
        AcDbLine first(oa,ia),last(ob,ib);
        result=create({&outside,&inside,&first,&last});
    } else {
        AcDbLine first(oa,centre),last(ob,centre);
        result=create({&outside,&first,&last});
    }
    unite(result,disk(a,radius));unite(result,disk(b,radius));return result;
}
void appendBands(AcDbEntity& entity,double radius,Mask& result,const Cancel& cancelled) {
    check(cancelled);
    if(++result.boundaryPieces>kMaximumBoundaryPieces)
        throw std::runtime_error("clearance boundary budget exceeded");
    if(auto* line=AcDbLine::cast(&entity)) unite(result.region,lineBand(*line,radius));
    else if(auto* arc=AcDbArc::cast(&entity)) unite(result.region,arcBand(*arc,radius));
    else if(auto* circle=AcDbCircle::cast(&entity)) unite(result.region,circleBand(*circle,radius));
    else if(AcDbPolyline::cast(&entity)||AcDbRegion::cast(&entity)) {
        AcDbVoidPtrArray raw;
        const auto status=entity.explode(raw);
        std::vector<Entity> children;
        for(void* item:raw) children.emplace_back(static_cast<AcDbEntity*>(item));
        require(status,"clearance native explode");
        if(children.empty()) throw std::runtime_error("empty clearance boundary");
        for(auto& child:children) {
            if(!child) throw std::runtime_error("null clearance boundary");
            appendBands(*child,radius,result,cancelled);
        }
    } else throw std::runtime_error("unsupported clearance curve: "+std::string(AcString(entity.isA()->name()).utf8Str()));
}
Entity worldCopy(AcDbEntity& entity,const AcGeMatrix3d& world) {
    for(int i=0;i<4;++i) for(int j=0;j<4;++j)
        if(!std::isfinite(world(i,j))) throw std::runtime_error("nonfinite clearance transform");
    AcDbEntity* raw=nullptr;
    const auto status=entity.getTransformedCopy(world,raw);
    Entity copy(raw);require(status,"clearance world copy");
    if(!copy) throw std::runtime_error("null clearance world copy");
    return copy;
}
}
double area(const AcDbRegion& region) {
    double value=0;require(region.getArea(value),"clearance area");
    if(!std::isfinite(value)||value<0) throw std::runtime_error("invalid clearance area");
    return value;
}
Region cloneRegion(const AcDbRegion& source) {
    auto* copy=AcDbRegion::cast(source.clone());
    if(!copy) throw std::runtime_error("clearance region clone failed");
    return Region(copy);
}
Region polygon(const std::vector<AcGePoint3d>& vertices) {
    if(vertices.size()<3||vertices.size()>kMaximumBoundaryPieces)
        throw std::runtime_error("clearance polygon vertex count");
    AcDbPolyline poly;
    for(unsigned i=0;i<vertices.size();++i) {
        const auto p=flat(vertices[i]);
        require(poly.addVertexAt(i,AcGePoint2d(p.x,p.y)),"clearance polygon vertex");
    }
    poly.setClosed(true);
    return create({&poly});
}
Mask aroundCurve(AcDbEntity& curve,const AcGeMatrix3d& world,double distance,const Cancel& cancelled) {
    validDistance(distance);check(cancelled);
    if(distance==0) return {}; // a line with zero width has zero excluded area
    auto copy=worldCopy(curve,world);
    Mask result;appendBands(*copy,distance,result,cancelled);return result;
}
Mask aroundArea(AcDbRegion& source,const AcGeMatrix3d& world,double distance,const Cancel& cancelled) {
    validDistance(distance);check(cancelled);
    double z=0;
    { // Check the actual native supporting plane before projecting XY.
        ga::nativeQuery::AffineAreaQuery query;query.prepare(source,world);
        z=query.evidence().worldZ;
    }
    auto copy=worldCopy(source,world);
    auto* region=AcDbRegion::cast(copy.get());
    if(!region) throw std::runtime_error("world clearance area not REGION");
    require(region->transformBy(AcGeMatrix3d::translation({0,0,-z})),"clearance plane translation");
    Mask result;result.region=cloneRegion(*region);
    if(distance>0) appendBands(*region,distance,result,cancelled);
    return result;
}
Region subtract(const AcDbRegion& work,const AcDbRegion& obstacle) {
    auto result=cloneRegion(work),other=cloneRegion(obstacle);
    require(result->booleanOper(AcDb::kBoolSubtract,other.get()),"clearance difference");
    if(result->isNull()) return nullptr;
    if(area(*result)==0) return nullptr;
    return result;
}
}
