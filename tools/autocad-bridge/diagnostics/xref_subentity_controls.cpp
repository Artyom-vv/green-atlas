// Verify that a traverser's child retains the complete instance path before
// trusting its world-space edge/face queries. Never infer the path from XYZ.
#include "xref_subentity_controls.h"
#include "file_io.h"
#include "AcString.h"
#include "dbsubeid.h"
#include "brbetrav.h"
#include "brbftrav.h"
#include "bredge.h"
#include "brface.h"
#include "geintrvl.h"
#include <cmath>
#include <memory>

namespace ga::xref {
namespace {
std::string q(const std::string& s) { return "\""+ga::bridge::jsonEscape(s)+"\""; }
void path(std::ostream& out,const AcBrEntity& entity,bool useGet=false) {
    AcDbFullSubentPath result;
    const auto status=useGet?entity.get(result):entity.getSubentPath(result);
    out<<"{\"status\":"<<int(status)<<",\"handles\":[";
    const auto& ids=result.objectIds();
    for(int i=0;i<ids.length();++i) {
        ACHAR value[32]={}; ids[i].handle().getIntoAsciiBuffer(value);
        out<<(i?",":"")<<q(AcString(value).utf8Str());
    }
    out<<"],\"subentity_type\":"<<int(result.subentId().type())<<'}';
}
void witness(std::ostream& out,const AcBrEdge& edge,const AcBrBrep& world) {
    AcGeCurve3d* raw=nullptr;
    const auto status=edge.getCurve(raw);
    std::unique_ptr<AcGeCurve3d> curve(raw);
    out<<"{\"curve_status\":"<<int(status)<<",\"curve_returned\":"<<(curve?"true":"false");
    // A warning plus a returned curve is NOT proof of a world-space curve.
    if(status==AcBr::eOk && curve) {
        AcGeInterval interval; curve->getInterval(interval);
        if(interval.isBounded() && std::isfinite(interval.lowerBound()) && std::isfinite(interval.upperBound())) {
            const auto point=curve->evalPoint((interval.lowerBound()+interval.upperBound())/2);
            if(std::isfinite(point.x)&&std::isfinite(point.y)&&std::isfinite(point.z)) {
                const auto answer=ga::direct::membership(world,point);
                out<<",\"point\":["<<point.x<<','<<point.y<<','<<point.z<<']'
                    <<",\"containment_status\":"<<answer.status<<",\"membership\":"<<q(answer.membership);
            }
        }
    }
    out<<'}';
}
void area(std::ostream& out,const AcBrFace& face) {
    double value=0; const auto status=face.getArea(value);
    out<<"{\"status\":"<<int(status)<<",\"value\":";
    if(status==AcBr::eOk && std::isfinite(value)) out<<value; else out<<"null";
    out<<'}';
}
}
void inspectSubentityPaths(const ga::direct::Prepared& prepared,
                          AcDbEntity& entity,const AcDbObjectIdArray& parents,
                          std::ostream& out) {
    if(!AcDbRegion::cast(&entity)) return;
    AcDbObjectIdArray ids=parents; ids.append(entity.objectId());
    out<<",\"subentity_path_probe\":{\"world_brep_path\":"; path(out,prepared.world);
    out<<",\"world_brep_get\":"; path(out,prepared.world,true);
    AcBrBrepEdgeTraverser edges;
    const auto start=edges.setBrep(prepared.world);
    out<<",\"edge_traverser_status\":"<<int(start)<<",\"edges\":[";
    for(unsigned i=0;start==AcBr::eOk&&!edges.done()&&i<4;++i) {
        AcBrEdge edge; const auto opened=edges.getEdge(edge);
        out<<(i?",":"")<<"{\"get_edge_status\":"<<int(opened);
        if(opened==AcBr::eOk) {
            out<<",\"returned_path\":"; path(out,edge);
            out<<",\"returned_get\":"; path(out,edge,true);
            out<<",\"as_returned\":"; witness(out,edge,prepared.world);
            AcDbFullSubentPath original;
            const auto obtained=edge.get(original);
            if(obtained==AcBr::eOk) {
                AcBrEdge explicitEdge;
                const auto bound=explicitEdge.set(AcDbFullSubentPath(ids,original.subentId()));
                out<<",\"explicit_set_status\":"<<int(bound);
                if(bound==AcBr::eOk) {
                    out<<",\"explicit_path\":"; path(out,explicitEdge);
                    out<<",\"explicit_query\":"; witness(out,explicitEdge,prepared.world);
                }
            }
        }
        out<<'}'; if(edges.next()!=AcBr::eOk) break;
    }
    out<<"],\"faces\":[";
    AcBrBrepFaceTraverser faces; const auto faceStart=faces.setBrep(prepared.world);
    for(unsigned i=0;faceStart==AcBr::eOk&&!faces.done()&&i<4;++i) {
        AcBrFace face; const auto opened=faces.getFace(face);
        out<<(i?",":"")<<"{\"get_face_status\":"<<int(opened);
        if(opened==AcBr::eOk) {
            out<<",\"returned_path\":"; path(out,face);
            out<<",\"returned_get\":"; path(out,face,true);
            out<<",\"as_returned\":"; area(out,face);
            AcDbFullSubentPath original; const auto obtained=face.get(original);
            if(obtained==AcBr::eOk) {
                AcBrFace explicitFace;
                const auto bound=explicitFace.set(AcDbFullSubentPath(ids,original.subentId()));
                out<<",\"explicit_set_status\":"<<int(bound);
                if(bound==AcBr::eOk) { out<<",\"explicit_query\":"; area(out,explicitFace); }
            }
        }
        out<<'}'; if(faces.next()!=AcBr::eOk) break;
    }
    out<<"],\"face_traverser_status\":"<<int(faceStart)<<'}';
}
}
