#include "xref_line_controls.h"
#include "file_io.h"
#include "AcDbCompoundObjectId.h"
#include "dbdict.h"
#include "dbspfilt.h"
#include "dbsymtb.h"
#include "AcString.h"
#include <algorithm>
#include <cmath>
#include <iomanip>
#include <memory>
#include <stdexcept>

namespace ga::xref {
namespace {
std::string q(const std::string& value) { return "\""+ga::bridge::jsonEscape(value)+"\""; }
void xyz(std::ostream& out,const AcGePoint3d& p) { out<<'['<<p.x<<','<<p.y<<','<<p.z<<']'; }
template<class T> struct Close { void operator()(T* p) const { if(p) p->close(); } };
template<class T> using Read=std::unique_ptr<T,Close<T>>;
template<class T> Read<T> read(AcDbObjectId id) {
    T* object=nullptr;
    if(acdbOpenObject(object,id,AcDb::kForRead)!=Acad::eOk || !object) throw std::runtime_error("clip dictionary read failure");
    return Read<T>(object);
}
std::string kind(const AcGeMatrix3d& m) {
    const AcGeVector3d x(m(0,0),m(1,0),m(2,0)),y(m(0,1),m(1,1),m(2,1)),z(m(0,2),m(1,2),m(2,2));
    std::string result;
    if(std::abs(m(0,3))+std::abs(m(1,3))+std::abs(m(2,3))>1e-8) result+="translated;";
    if(std::abs(m(0,1))+std::abs(m(1,0))+std::abs(m(0,2))+std::abs(m(2,0))>1e-8) result+="rotated;";
    if(x.crossProduct(y).dotProduct(z)<0) result+="mirrored;";
    if(std::abs(x.length()-1)+std::abs(y.length()-1)+std::abs(z.length()-1)>1e-8) result+="scaled;";
    if(std::abs(x.length()-y.length())+std::abs(x.length()-z.length())>1e-8) result+="nonuniform;";
    return result.empty()?"identity":result;
}
double finiteLineDistance(const AcGePoint3d& a,const AcGePoint3d& b,const AcGePoint3d& p) {
    const double dx=b.x-a.x,dy=b.y-a.y;
    const double t=std::clamp(((p.x-a.x)*dx+(p.y-a.y)*dy)/(dx*dx+dy*dy),0.0,1.0);
    return std::hypot(p.x-a.x-t*dx,p.y-a.y-t*dy);
}
}
std::string transformKind(const AcGeMatrix3d& matrix) { return kind(matrix); }
void LineControls::inspect(AcDbEntity& entity,const AcDbObjectIdArray& parents,
                           const AcGeMatrix3d& traversal,AcDbDatabase& host,
                           const std::string& route,const std::string& context) {
    auto* line=AcDbLine::cast(&entity);
    if(!line||parents.isEmpty()) return;
    const auto category=kind(traversal);
    auto& observed=counts[context+":"+category];
    if(observed++>=2 || tested>=100) return;
    rows<<std::setprecision(17)<<(tested++?",":"")<<"{\"route\":"<<q(route)
        <<",\"context\":"<<q(context)<<",\"transform_kind\":"<<q(category);
    try {
        AcDbCompoundObjectId compound;
        auto status=compound.set(entity.objectId(),parents,&host);
        AcGeMatrix3d native;
        if(status==Acad::eOk) status=compound.getTransform(native);
        if(status!=Acad::eOk || compound.status()!=AcDbCompoundObjectId::kValid)
            throw std::runtime_error("compound transform status "+std::to_string(int(status)));
        double matrixDelta=0;
        for(int i=0;i<4;++i) for(int j=0;j<4;++j)
            matrixDelta=std::max(matrixDelta,std::abs(native(i,j)-traversal(i,j)));
        AcDbEntity* raw=nullptr;
        status=line->getTransformedCopy(native,raw);
        std::unique_ptr<AcDbEntity> owned(raw);
        auto* world=AcDbLine::cast(raw);
        if(status!=Acad::eOk || !world) throw std::runtime_error("native line copy status "+std::to_string(int(status)));
        const auto a=world->startPoint(),b=world->endPoint();
        auto expectedA=line->startPoint(),expectedB=line->endPoint();
        expectedA.transformBy(traversal); expectedB.transformBy(traversal);
        const double endpointDelta=std::max(expectedA.distanceTo(a),expectedB.distanceTo(b));
        const double length=std::hypot(b.x-a.x,b.y-a.y);
        if(!std::isfinite(length)||length<1e-9) throw std::runtime_error("degenerate XY line control");
        double maxDistanceDelta=0; unsigned queries=0,failures=0,heightMismatches=0;
        // Independent finite-segment formula, including beyond both endpoints.
        for(double t:{-0.25,0.0,0.25,0.5,1.0,1.25}) for(double offset:{-2.0,0.0,0.99,1.01}) {
            AcGePoint3d p(a.x+t*(b.x-a.x)-offset*(b.y-a.y)/length,
                          a.y+t*(b.y-a.y)+offset*(b.x-a.x)/length,0);
            AcGePoint3d nearest,highNearest;
            const auto answer=world->getClosestPointTo(p,AcGeVector3d::kZAxis,nearest,false);
            p.z=150;
            const auto high=world->getClosestPointTo(p,AcGeVector3d::kZAxis,highNearest,false);
            queries+=2;
            if(answer!=Acad::eOk || high!=Acad::eOk) { ++failures; continue; }
            if(!std::isfinite(nearest.x)||!std::isfinite(nearest.y)||!std::isfinite(nearest.z)
               ||!std::isfinite(highNearest.x)||!std::isfinite(highNearest.y)||!std::isfinite(highNearest.z)) {
                ++failures; continue;
            }
            const double measured=std::hypot(p.x-nearest.x,p.y-nearest.y);
            maxDistanceDelta=std::max(maxDistanceDelta,std::abs(measured-finiteLineDistance(a,b,p)));
            if(std::hypot(nearest.x-highNearest.x,nearest.y-highNearest.y)>1e-8) ++heightMismatches;
        }
        const bool passed=matrixDelta<=1e-8 && endpointDelta<=1e-8 && maxDistanceDelta<=1e-8
                          && failures==0 && heightMismatches==0;
        rows<<",\"passed\":"<<(passed?"true":"false")<<",\"queries\":"<<queries
            <<",\"query_failures\":"<<failures<<",\"height_mismatches\":"<<heightMismatches
            <<",\"matrix_delta\":"<<matrixDelta<<",\"endpoint_delta\":"<<endpointDelta
            <<",\"max_distance_delta\":"<<maxDistanceDelta<<",\"world_start\":";
        xyz(rows,a); rows<<",\"world_end\":"; xyz(rows,b);
    } catch(const std::exception& e) { rows<<",\"passed\":false,\"error\":"<<q(e.what()); }
    rows<<'}';
}
void clipState(std::ostream& out,const AcDbBlockReference& ref) {
    out<<"{\"entity_visible\":"<<(ref.visibility()==AcDb::kVisible?"true":"false");
    try {
        auto layer=read<AcDbLayerTableRecord>(ref.layerId());
        out<<",\"layer_off\":"<<(layer->isOff()?"true":"false")
            <<",\"layer_frozen\":"<<(layer->isFrozen()?"true":"false");
        const auto id=ref.extensionDictionary();
        if(id.isNull()) { out<<",\"filter_present\":false}"; return; }
        auto ext=read<AcDbDictionary>(id); AcDbObjectId filters;
        if(ext->getAt(_T("ACAD_FILTER"),filters)!=Acad::eOk) { out<<",\"filter_present\":false}"; return; }
        auto dict=read<AcDbDictionary>(filters); AcDbObjectId spatial;
        if(dict->getAt(_T("SPATIAL"),spatial)!=Acad::eOk) { out<<",\"filter_present\":false}"; return; }
        auto clip=read<AcDbSpatialFilter>(spatial);
        AcGePoint2dArray points; AcGeVector3d normal; double elevation=0,front=0,back=0; Adesk::Boolean enabled=false;
        const auto status=clip->getDefinition(points,normal,elevation,front,back,enabled);
        out<<",\"filter_present\":true,\"status\":"<<int(status)
            <<",\"enabled\":"<<(enabled?"true":"false")<<",\"inverted\":"<<(clip->isInverted()?"true":"false")
            <<",\"vertices\":"<<points.length();
    } catch(const std::exception& e) { out<<",\"error\":"<<q(e.what()); }
    out<<'}';
}
}
