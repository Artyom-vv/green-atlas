// Diagnostic of survey-clipped authored chains. Both native boundary alternatives
// are returned as proposals; this never chooses which side is a building.
#include "rxregsvc.h"
#include "core_rxmfcapi.h"
#include "aced.h"
#include "adslib.h"
#include "AcString.h"
#include "dbcurve.h"
#include "dbsymtb.h"
#include "dbidmap.h"
#include "native_area_group.h"
#include "xref_instance_access.h"
#include "file_io.h"
#include <algorithm>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace {
constexpr double kEquality=1e-8;
std::string q(const std::string& s) {return "\""+ga::bridge::jsonEscape(s)+"\"";}
std::string line(std::istream& in) {
    std::string v;if(!std::getline(in,v)||v.empty()||v.size()>4096) throw std::runtime_error("request line");return v;
}
void check(Acad::ErrorStatus status,const char* op) {
    if(status!=Acad::eOk) throw std::runtime_error(std::string(op)+":"+std::to_string(int(status)));
}
std::pair<AcGePoint3d,AcGePoint3d> endpoints(AcDbEntity* entity) {
    auto* curve=AcDbCurve::cast(entity);if(!curve)throw std::runtime_error("not curve");
    AcGePoint3d a,b;check(curve->getStartPoint(a),"start");check(curve->getEndPoint(b),"end");return {a,b};
}
void point(std::ostream& out,const AcGePoint3d& p) {out<<'['<<p.x<<','<<p.y<<','<<p.z<<']';}
void command() {
    ACHAR requestPath[4096]={};if(acedGetString(1,_T("\nClipped group request: "),requestPath)!=RTNORM)return;
    try {
        std::ifstream in(AcString(requestPath).utf8Str());
        const auto output=line(in),boundaryRoute=line(in);
        if(std::ifstream(output).good())throw std::runtime_error("output already exists");
        const auto count=std::stoi(line(in));if(count<1||count>128)throw std::runtime_error("group budget");
        auto* host=acdbHostApplicationServices()->workingDatabase();
        resbuf before{};if(acedGetVar(_T("DBMOD"),&before)!=RTNORM)throw std::runtime_error("DBMOD");
        ga::xref::Instance boundary;ga::xref::resolve(*host,boundaryRoute,boundary);
        auto* boundaryCurve=AcDbCurve::cast(boundary.entity);
        if(!boundaryCurve)throw std::runtime_error("boundary must be a curve");
        const auto boundaryEnds=endpoints(boundary.entity);
        if(boundaryEnds.first.distanceTo(boundaryEnds.second)>kEquality)
            throw std::runtime_error("boundary endpoints are not coincident");
        ga::nativeQuery::AffineAreaQuery boundaryArea;
        boundaryArea.prepare(*boundary.entity,boundary.transform);
        std::vector<std::unique_ptr<ga::xref::Instance>> members;
        std::vector<AcDbEntity*> curves;
        std::vector<AcGePoint3d> ends;
        std::vector<std::string> routes;
        for(int i=0;i<count;++i) {
            const auto route=line(in);auto item=std::make_unique<ga::xref::Instance>();
            ga::xref::resolve(*host,route,*item);
            if(item->entity->ownerId()!=boundary.entity->ownerId()
                ||route.substr(0,route.find_last_of('/'))!=boundaryRoute.substr(0,boundaryRoute.find_last_of('/')))
                throw std::runtime_error("not one instance/definition");
            auto [a,b]=endpoints(item->entity);ends.push_back(a);ends.push_back(b);
            curves.push_back(item->entity);members.push_back(std::move(item));routes.push_back(route);
        }
        std::vector<AcGePoint3d> unmatched;
        for(std::size_t i=0;i<ends.size();++i) {
            unsigned matched=0;
            for(std::size_t j=0;j<ends.size();++j) if(i!=j&&ends[i].distanceTo(ends[j])<=kEquality)++matched;
            if(!matched)unmatched.push_back(ends[i]);
            if(matched>1)throw std::runtime_error("ambiguous source chain");
        }
        if(unmatched.size()!=2)throw std::runtime_error("chain must have exactly two open ends");
        const auto pointCount=std::stoi(line(in));if(pointCount<1||pointCount>64)throw std::runtime_error("point budget");
        std::vector<AcGePoint3d> points;
        for(int i=0;i<pointCount;++i) {
            AcGePoint3d p;std::istringstream row(line(in));if(!(row>>p.x>>p.y>>p.z))throw std::runtime_error("point");points.push_back(p);
        }
        // Native DB-to-DB clone translates symbolic ObjectIds. Splitting the
        // XREF curve directly left source-DB IDs in its pieces (eWrongDatabase).
        // No source geometry is re-encoded; AutoCAD owns this translation.
        AcDbDatabase scratch(true,true);
        ga::direct::ReadEntities readParts;
        AcDbBlockTable* table=nullptr;check(scratch.getBlockTable(table,AcDb::kForRead),"scratch table");
        AcDbObjectId modelId;const auto modelStatus=table->getAt(ACDB_MODEL_SPACE,modelId);
        table->close();check(modelStatus,"scratch model ID");
        AcDbObjectIdArray originalIds;originalIds.append(boundary.entity->objectId());
        for(auto* curve:curves)originalIds.append(curve->objectId());
        AcDbIdMapping mapping;
        check(host->wblockCloneObjects(originalIds,modelId,mapping,AcDb::kDrcIgnore,false),"native scratch clone");
        std::vector<AcDbEntity*> clones;
        for(const auto& sourceId:originalIds) {
            AcDbIdPair pair;pair.setKey(sourceId);
            if(!mapping.compute(pair)||!pair.isCloned())throw std::runtime_error("incomplete native clone map");
            AcDbEntity* clone=nullptr;check(acdbOpenObject(clone,pair.value(),AcDb::kForRead),"clone read");
            readParts.values.push_back(clone);clones.push_back(clone);
        }
        boundaryCurve=AcDbCurve::cast(clones[0]);
        curves.assign(clones.begin()+1,clones.end());
        std::vector<double> parameters;
        std::vector<double> joinDistances;
        for(const auto& p:unmatched) {
            AcGePoint3d nearest;check(boundaryCurve->getClosestPointTo(p,nearest,false),"boundary nearest");
            const auto distance=p.distanceTo(nearest);if(distance>kEquality)throw std::runtime_error("endpoint not on survey boundary");
            double t;check(boundaryCurve->getParamAtPoint(nearest,t),"boundary parameter");
            parameters.push_back(t);joinDistances.push_back(distance);
        }
        std::sort(parameters.begin(),parameters.end());
        AcGeDoubleArray params;for(auto t:parameters)params.append(t);
        AcDbVoidPtrArray parts;
        const auto splitStatus=boundaryCurve->getSplitCurves(params,parts);
        std::vector<std::unique_ptr<AcDbEntity>> ownedParts;
        for(void* raw:parts)ownedParts.emplace_back(static_cast<AcDbEntity*>(raw));
        check(splitStatus,"native boundary split");
        if(parts.length()!=2&&parts.length()!=3)throw std::runtime_error("unexpected closed boundary partition");
        // getSplitCurves produces transient objects open for write. Append only
        // to a disposable DB and reopen FOR READ before createFromCurves.
        AcDbBlockTableRecord* model=nullptr;check(acdbOpenObject(model,modelId,AcDb::kForWrite),"scratch model");
        std::vector<AcDbEntity*> splitParts;
        for(auto& part:ownedParts) {
            AcDbObjectId id;const auto append=model->appendAcDbEntity(id,part.get());
            if(append!=Acad::eOk){model->close();check(append,"scratch append");}
            part.release()->close();AcDbEntity* entity=nullptr;
            const auto read=acdbOpenObject(entity,id,AcDb::kForRead);
            if(read!=Acad::eOk){model->close();check(read,"scratch read");}
            readParts.values.push_back(entity);
            splitParts.push_back(entity);
        }
        model->close();
        const std::vector<std::vector<unsigned>> alternatives=parts.length()==2
            ?std::vector<std::vector<unsigned>>{{0},{1}}:std::vector<std::vector<unsigned>>{{1},{0,2}};
        std::ostringstream out;out<<std::setprecision(17);
        out<<"{\"scope\":\"native survey-boundary alternatives; no semantic side selection\",\"boundary\":"<<q(boundaryRoute)
            <<",\"join_distances\":["<<joinDistances[0]<<','<<joinDistances[1]<<"],\"split_parts\":"<<parts.length()<<",\"proposals\":[";
        for(std::size_t i=0;i<alternatives.size();++i) {
            out<<(i?",":"")<<"{\"index\":"<<i;
            try {
                auto group=curves;
                for(auto index:alternatives[i])group.push_back(splitParts[index]);
                ga::nativeQuery::AreaGroupQuery query;query.prepare(group,boundary.transform);
                out<<",\"native_area\":"<<query.evidence().localArea*query.evidence().jacobian<<",\"answers\":[";
                for(std::size_t j=0;j<points.size();++j) {
                    const auto answer=query.queryPlanar(points[j]);
                    out<<(j?",":"")<<"{\"point\":";point(out,points[j]);
                    out<<",\"status\":"<<answer.status<<",\"membership\":"<<q(answer.membership)
                        <<",\"distance\":"<<answer.distance<<'}';
                }
                out<<']';
            }catch(const std::exception& error){out<<",\"error\":"<<q(error.what());}
            out<<'}';
        }
        resbuf after{};acedGetVar(_T("DBMOD"),&after);
        out<<"],\"dbmod_before\":"<<before.resval.rint<<",\"dbmod_after\":"<<after.resval.rint<<'}';
        if(before.resval.rint!=after.resval.rint)throw std::runtime_error("host DBMOD changed");
        if(!ga::bridge::writeAtomicText(output,out.str()))throw std::runtime_error("publish");
        acutPrintf(_T("\nNative clipped-group proposals written."));
    }catch(const std::exception& error){acutPrintf(_T("\nNative clipped-group failed: %s"),AcString(error.what()).kwszPtr());}
}
}
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode msg,void* id) {
    if(msg==AcRx::kInitAppMsg) {
        acrxLoadModule(_T("AcGeomentObj.dbx"),0);acrxDynamicLinker->loadModule(_T("AcBr.dbx"),1);
        acrxDynamicLinker->unlockApplication(id);acrxDynamicLinker->registerAppMDIAware(id);
        acedRegCmds->addCommand(_T("GA_CLIPPED_GROUP_PROBE"),_T("GAGROUPCLIP"),_T("GAGROUPCLIP"),ACRX_CMD_MODAL,command);
    } else if(msg==AcRx::kUnloadAppMsg)acedRegCmds->removeGroup(_T("GA_CLIPPED_GROUP_PROBE"));
    return AcRx::kRetOK;
}
