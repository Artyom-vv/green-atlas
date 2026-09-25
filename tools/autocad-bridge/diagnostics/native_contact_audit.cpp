// Read-only research: native endpoint contacts and nearby curve identities.
// This is not a product importer, repair, or automatic semantic decision.
#include "rxregsvc.h"
#include "core_rxmfcapi.h"
#include "aced.h"
#include "acedCmdNF.h"
#include "adslib.h"
#include "AcString.h"
#include "dbcurve.h"
#include "dbsymtb.h"
#include "dbidmap.h"
#include "xref_instance_access.h"
#include "file_io.h"
#include <algorithm>
#include <fstream>
#include <iomanip>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>

namespace {
constexpr double kContactTolerance = 1e-8;
constexpr double kNearTolerance = .02;
std::string q(const std::string& s) { return "\""+ga::bridge::jsonEscape(s)+"\""; }
std::string line(std::istream& in) {
    std::string s; if(!std::getline(in,s)||s.empty()) throw std::runtime_error("request line"); return s;
}
void check(Acad::ErrorStatus s,const char* op) {
    if(s!=Acad::eOk) throw std::runtime_error(std::string(op)+":"+std::to_string(int(s)));
}
void point(std::ostream& out,const AcGePoint3d& p) {out<<'['<<p.x<<','<<p.y<<','<<p.z<<']';}
struct Curve {
    std::string route,layer;
    std::unique_ptr<ga::xref::Instance> instance;
    std::unique_ptr<AcDbCurve> world;
    AcGePoint3d a,b;
    AcDbExtents bounds;
    double start=0,end=0,length=0;
};
bool nearBox(const AcGePoint3d& p,const AcDbExtents& box,double tolerance) {
    return p.x>=box.minPoint().x-tolerance && p.x<=box.maxPoint().x+tolerance
        && p.y>=box.minPoint().y-tolerance && p.y<=box.maxPoint().y+tolerance;
}
void command() {
    ACHAR path[4096]={}; if(acedGetString(1,_T("\nContact audit request: "),path)!=RTNORM)return;
    try {
        std::ifstream in(AcString(path).utf8Str()); const auto output=line(in);
        if(std::ifstream(output).good())throw std::runtime_error("output exists");
        auto* host=acdbHostApplicationServices()->workingDatabase();
        resbuf before{};acedGetVar(_T("DBMOD"),&before);
        const auto targetCount=std::stoi(line(in)); std::set<std::string> targets;
        for(int i=0;i<targetCount;++i) targets.insert(line(in));
        const auto count=std::stoi(line(in)); if(count>20000)throw std::runtime_error("candidate budget");
        std::vector<Curve> curves; std::vector<std::pair<std::string,std::string>> errors;
        for(int i=0;i<count;++i) {
            const auto route=line(in),layer=line(in);
            try {
                Curve c;c.route=route;c.layer=layer;c.instance=std::make_unique<ga::xref::Instance>();
                ga::xref::resolve(*host,route,*c.instance);
                auto* native=AcDbCurve::cast(c.instance->entity); if(!native)throw std::runtime_error("not curve");
                c.world.reset(AcDbCurve::cast(native->clone()));if(!c.world)throw std::runtime_error("clone");
                check(c.world->transformBy(c.instance->transform),"world transform");
                check(c.world->getStartPoint(c.a),"start");check(c.world->getEndPoint(c.b),"end");
                check(c.world->getStartParam(c.start),"start parameter");check(c.world->getEndParam(c.end),"end parameter");
                double s=0,e=0;check(c.world->getDistAtParam(c.start,s),"start distance");
                check(c.world->getDistAtParam(c.end,e),"end distance");c.length=e-s;
                check(c.world->getGeomExtents(c.bounds),"extents");
                // Do not accumulate thousands of read opens on the same INSERT.
                c.instance.reset();
                curves.push_back(std::move(c));
            }catch(const std::exception& e){errors.push_back({route,e.what()});}
        }
        std::ostringstream out;out<<std::setprecision(17)<<"{\"scope\":\"read-only native contacts, no automatic areas\",\"candidate_count\":"<<curves.size()<<",\"errors\":[";
        for(std::size_t i=0;i<errors.size();++i)out<<(i?",":"")<<"{\"route\":"<<q(errors[i].first)<<",\"error\":"<<q(errors[i].second)<<'}';
        out<<"],\"curves\":[";bool first=true;
        for(auto& c:curves) {
            out<<(first?"":",")<<"{\"route\":"<<q(c.route)<<",\"layer\":"<<q(c.layer)<<",\"length\":"<<c.length<<",\"start\":";
            first=false;point(out,c.a);out<<",\"end\":";point(out,c.b);out<<'}';
        }
        out<<"],\"targets\":[";first=true;
        for(auto& c:curves) if(targets.count(c.route)) {
            out<<(first?"":",")<<"{\"route\":"<<q(c.route)<<",\"endpoints\":[";first=false;
            for(int endpoint=0;endpoint<2;++endpoint) {
                const auto p=endpoint?c.b:c.a;out<<(endpoint?",":"")<<"{\"point\":";point(out,p);
                out<<",\"contacts\":[";bool firstContact=true;
                for(auto& other:curves) {
                    if(other.route==c.route||!nearBox(p,other.bounds,kNearTolerance))continue;
                    AcGePoint3d nearest;const auto status=other.world->getClosestPointTo(p,nearest,false);
                    if(status!=Acad::eOk)continue;const auto distance=p.distanceTo(nearest);
                    if(distance>kNearTolerance)continue;
                    const bool atEndpoint=nearest.distanceTo(other.a)<=kContactTolerance||nearest.distanceTo(other.b)<=kContactTolerance;
                    double param=0;const auto ps=other.world->getParamAtPoint(nearest,param);
                    out<<(firstContact?"":",")<<"{\"route\":"<<q(other.route)<<",\"layer\":"<<q(other.layer)
                       <<",\"distance\":"<<distance<<",\"at_endpoint\":"<<(atEndpoint?"true":"false")
                       <<",\"parameter_status\":"<<int(ps)<<",\"parameter\":"<<param<<",\"nearest\":";
                    point(out,nearest);out<<'}';firstContact=false;
                }
                out<<"]}";
            }
            out<<"]}";
        }
        resbuf after{};acedGetVar(_T("DBMOD"),&after);
        out<<"],\"dbmod_before\":"<<before.resval.rint<<",\"dbmod_after\":"<<after.resval.rint<<'}';
        if(before.resval.rint!=after.resval.rint)throw std::runtime_error("DBMOD changed");
        if(!ga::bridge::writeAtomicText(output,out.str()))throw std::runtime_error("publish");
        acutPrintf(_T("\nContact audit completed."));
    }catch(const std::exception& e){acutPrintf(_T("\nContact audit failed: %s"),AcString(e.what()).kwszPtr());}
}
void boundary() {
    ACHAR path[4096]={};if(acedGetString(1,_T("\nBoundary output: "),path)!=RTNORM)return;
    const std::string output=AcString(path).utf8Str();if(std::ifstream(output).good())return;
    // The runner creates a rectangle ONLY in a disposable control document.
    AcDbVoidPtrArray results;const auto status=acedTraceBoundary(AcGePoint3d(5,5,0),false,results);
    std::ostringstream out;out<<"{\"status\":"<<int(status)<<",\"count\":"<<results.length()<<",\"areas\":[";
    for(int i=0;i<results.length();++i){auto* e=static_cast<AcDbEntity*>(results[i]);double area=0;
        auto* c=AcDbCurve::cast(e);const auto s=c?c->getArea(area):Acad::eInvalidInput;
        out<<(i?",":"")<<"{\"status\":"<<int(s)<<",\"area\":"<<area<<'}';delete e;}
    out<<"]}";ga::bridge::writeAtomicText(output,out.str());
}
void boundaryReal() {
    ACHAR path[4096]={};if(acedGetString(1,_T("\nBoundary research output: "),path)!=RTNORM)return;
    const std::string output=AcString(path).utf8Str();if(std::ifstream(output).good())return;
    try {
        // Invoked ONLY in an isolated, disposable copy by run_contact_audit.py.
        auto* host=acdbHostApplicationServices()->workingDatabase();AcDbLayerTable* table=nullptr;
        check(host->getLayerTable(table,AcDb::kForRead),"layer table");AcDbLayerTableIterator* raw=nullptr;
        check(table->newIterator(raw),"layer iterator");table->close();std::unique_ptr<AcDbLayerTableIterator> it(raw);
        for(;!it->done();it->step()){
            AcDbLayerTableRecord* layer=nullptr;check(it->getRecord(layer,AcDb::kForWrite),"layer record");
            AcString name;layer->getName(name);const std::string value=name.utf8Str();
            const bool keep=value=="0"||value=="00.1_10004141_Топография|Здания"||value=="00.1_10004141_Топография|Граница заказа";
            layer->setIsOff(!keep);if(keep)layer->setIsFrozen(false);layer->close();
        }
        resbuf tile{};tile.restype=RTSHORT;tile.resval.rint=1;
        if(acedSetVar(_T("TILEMODE"),&tile)!=RTNORM)throw std::runtime_error("switch disposable copy to model space");
        acedCommandS(RTSTR,_T("_.UCS"),RTSTR,_T("_World"),RTNONE);
        acedCommandS(RTSTR,_T("_.REGENALL"),RTNONE);
        ads_point low={15600,-5900,0},high={16300,-4800,0};
        acedCommandS(RTSTR,_T("_.ZOOM"),RTSTR,_T("_Window"),RT3DPOINT,low,RT3DPOINT,high,RTNONE);
        const std::vector<std::pair<std::string,AcGePoint3d>> seeds={
            {"clipped_2931",{15994.606306,-5245.249309,0}},
            {"long_chain_1095",{15986,-4975,0}},
            {"closed_with_tail_AE50",{16087,-5231,0}},
            {"outside_control",{15610,-5000,0}}
        };
        std::ostringstream out;out<<std::setprecision(17)<<"{\"scope\":\"BOUNDARY in isolated copy, only buildings and order boundary visible\",\"cases\":[";bool first=true;
        for(double gap:{0.0,.02}){
            resbuf var{};var.restype=RTREAL;var.resval.rreal=gap;check(acedSetVar(_T("HPGAPTOL"),&var)==RTNORM?Acad::eOk:Acad::eInvalidInput,"HPGAPTOL");
            for(const auto& seed:seeds){
                AcDbVoidPtrArray boundaries;const auto status=acedTraceBoundary(seed.second,true,boundaries);
                out<<(first?"":",")<<"{\"label\":"<<q(seed.first)<<",\"gap_tolerance\":"<<gap<<",\"status\":"<<int(status)<<",\"areas\":[";first=false;
                for(int i=0;i<boundaries.length();++i){auto* entity=static_cast<AcDbEntity*>(boundaries[i]);auto* curve=AcDbCurve::cast(entity);double area=0;
                    const auto s=curve?curve->getArea(area):Acad::eInvalidInput;out<<(i?",":"")<<"{\"status\":"<<int(s)<<",\"area\":"<<area<<'}';delete entity;}
                out<<"]}";
            }
        }
        out<<"],\"residual_10FB\":";
        ga::xref::Instance residual,wall;ga::xref::resolve(*host,"6E16/10FB",residual);ga::xref::resolve(*host,"6E16/7BF",wall);
        auto* r=AcDbCurve::cast(residual.entity);auto* w=AcDbCurve::cast(wall.entity);AcGePoint3d start,nearest;
        if(!r||!w||residual.entity->ownerId()!=wall.entity->ownerId())throw std::runtime_error("residual probe identity");
        check(r->getStartPoint(start),"residual endpoint");check(w->getClosestPointTo(start,nearest,false),"residual nearest wall");
        out<<"{\"native_distance_to_7BF\":"<<start.distanceTo(nearest)<<",\"start\":";point(out,start);out<<",\"nearest\":";point(out,nearest);out<<"}}";
        if(!ga::bridge::writeAtomicText(output,out.str()))throw std::runtime_error("publish boundary research");
    }catch(const std::exception& e){acutPrintf(_T("\nBoundary research failed: %s"),AcString(e.what()).kwszPtr());}
}
}
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode msg,void* id) {
    if(msg==AcRx::kInitAppMsg) {
        acrxDynamicLinker->unlockApplication(id);acrxDynamicLinker->registerAppMDIAware(id);
        acedRegCmds->addCommand(_T("GA_CONTACT_AUDIT"),_T("GACONTACTAUDIT"),_T("GACONTACTAUDIT"),ACRX_CMD_MODAL,command);
        acedRegCmds->addCommand(_T("GA_CONTACT_AUDIT"),_T("GABOUNDARYCONTROL"),_T("GABOUNDARYCONTROL"),ACRX_CMD_MODAL,boundary);
        acedRegCmds->addCommand(_T("GA_CONTACT_AUDIT"),_T("GABOUNDARYREAL"),_T("GABOUNDARYREAL"),ACRX_CMD_MODAL,boundaryReal);
    }else if(msg==AcRx::kUnloadAppMsg)acedRegCmds->removeGroup(_T("GA_CONTACT_AUDIT"));
    return AcRx::kRetOK;
}
