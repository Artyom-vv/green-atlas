// Isolated checks of production native clearance geometry. No drawing writes.
#include "rxregsvc.h"
#include "core_rxmfcapi.h"
#include "aced.h"
#include "adslib.h"
#include "AcString.h"
#include "native_clearance.h"
#include "native_clearance_cache.h"
#include "native_affine_query.h"
#include "native_curve_query.h"
#include "xref_instance_access.h"
#include "file_io.h"
#include "dbents.h"
#include "dbpl.h"
#include "DbField.h"
#include "acdbxref.h"
#include <filesystem>
#include <chrono>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace {
using namespace ga::clearance;
void expect(bool value,const std::string& message) {if(!value) throw std::runtime_error(message);}
std::string q(const std::string& s) {return "\""+ga::bridge::jsonEscape(s)+"\"";}
std::string line(std::istream& in) {std::string s;expect(bool(std::getline(in,s)),"request line");return s;}
void near(double actual,double wanted,const char* name) {
    expect(std::abs(actual-wanted)<1e-6*std::max(1.,wanted),name);
}
void membership(AcDbRegion& region,const AcGePoint3d& point,bool inside,const char* name) {
    ga::nativeQuery::AffineAreaQuery query;query.prepare(region,AcGeMatrix3d::kIdentity);
    const auto answer=query.queryPlanar(point);
    expect(answer.status==0&&(answer.membership=="occupied")==inside,name);
}
unsigned controls() {
    unsigned count=0;
    const double pi=std::acos(-1.);
    for(double scale:{1.,1000.}) {
        auto square=polygon({{0,0,0},{10*scale,0,0},{10*scale,10*scale,0},{0,10*scale,0}});
        auto expanded=aroundArea(*square,AcGeMatrix3d::kIdentity,scale);
        near(area(*expanded.region),(100+40+pi)*scale*scale,"rounded rectangle area");
        membership(*expanded.region,{5*scale,5*scale,0},true,"building interior");
        membership(*expanded.region,{-0.5*scale,5*scale,0},true,"setback interior");
        membership(*expanded.region,{-1.1*scale,5*scale,0},false,"beyond setback");
        membership(*expanded.region,{-0.8*scale,-0.8*scale,0},false,"round not square corner");
        near(area(*square),100*scale*scale,"source region unchanged");++count;
        AcDbLine line({0,0,0},{10*scale,0,0});
        auto band=aroundCurve(line,AcGeMatrix3d::kIdentity,scale);
        near(area(*band.region),(20+pi)*scale*scale,"line capsule area");++count;
    }
    auto exterior=polygon({{0,0,0},{20,0,0},{20,20,0},{0,20,0}});
    auto courtyard=polygon({{5,5,0},{15,5,0},{15,15,0},{5,15,0}});
    auto building=subtract(*exterior,*courtyard);
    auto expanded=aroundArea(*building,AcGeMatrix3d::kIdentity,1);
    near(area(*expanded.region),400+80+pi-64,"courtyard buffer area");
    membership(*expanded.region,{10,10,0},false,"courtyard not filled");
    membership(*expanded.region,{5.5,10,0},true,"courtyard setback");++count;
    auto difference=subtract(*exterior,*expanded.region);
    near(area(*difference),64,"free courtyard intersection");++count;
    expect(!subtract(*courtyard,*exterior),"fully excluded zone is empty");++count;
    AcDbPolyline closed;
    closed.addVertexAt(0,{0,0});closed.addVertexAt(1,{10,0});
    closed.addVertexAt(2,{10,10});closed.addVertexAt(3,{0,10});closed.setClosed(true);
    auto ring=aroundCurve(closed,AcGeMatrix3d::kIdentity,1);
    membership(*ring.region,{5,5,0},false,"closed curve does not invent an area");++count;
    for(double radius:{1.,7.}) for(double direction:{1.,-1.}) for(double start:{0.,pi,1.75*pi}) for(double sweep:{pi/2,2*pi-1e-7}) {
        AcDbArc arc({0,0,0},{0,0,direction},5,start,std::fmod(start+sweep,2*pi));
        auto tube=aroundCurve(arc,AcGeMatrix3d::kIdentity,radius);
        ga::nativeQuery::CurveQuery original;original.prepare(arc,AcGeMatrix3d::kIdentity);
        ga::nativeQuery::AffineAreaQuery mask;mask.prepare(*tube.region,AcGeMatrix3d::kIdentity);
        for(int x=-13;x<=13;++x) for(int y=-13;y<=13;++y) {
            AcGePoint3d p(x+.127,y+.319,0);
            const auto d=original.queryPlanar(p),m=mask.queryPlanar(p);
            expect(d.distanceComplete&&m.status==0,"arc measurement complete");
            expect((d.distance<radius)==(m.membership=="occupied"),"arc tube distance equivalence");
        }
        ++count;
    }
    AcDbCircle circle({0,0,0},AcGeVector3d::kZAxis,5);
    auto circleTube=aroundCurve(circle,AcGeMatrix3d::kIdentity,1);
    near(area(*circleTube.region),pi*(36-16),"circle annulus");++count;
    auto elevated=AcGeMatrix3d::translation({100,200,30});
    auto moved=aroundArea(*exterior,elevated,1);
    membership(*moved.region,{110,210,0},true,"elevated world XY projection");++count;
    AcDbLine sloped({0,0,0},{10,0,1});bool rejected=false;
    try {aroundCurve(sloped,AcGeMatrix3d::kIdentity,1);} catch(const std::exception&) {rejected=true;}
    expect(rejected,"nonplanar source must be explicit failure");++count;
    bool cancelled=false;
    try {aroundArea(*exterior,AcGeMatrix3d::kIdentity,1,[]{return true;});} catch(const std::exception&) {cancelled=true;}
    expect(cancelled,"cancellation must not return partial mask");++count;
    Cache cache;
    ga::nativeQuery::ObjectTarget target{"A",ga::nativeQuery::QueryCapability::Area};
    unsigned builds=0;
    auto build=[&] {++builds;return aroundArea(*exterior,AcGeMatrix3d::kIdentity,1);};
    expect(!cache.get(target,1,build).hit,"first mask is cold");
    expect(cache.get(target,1,build).hit&&builds==1,"same mask reused");
    cache.get(target,2,build);expect(builds==2,"rule distance invalidates mask");
    target.faceId=1;cache.get(target,1,build);expect(builds==3,"derived face identity isolated");
    cache.clear();cache.get(target,1,build);expect(builds==4&&cache.size()==1,"capture reset clears masks");++count;
    target.faceId=2;
    bool cancel=false;
    try {cache.get(target,1,[&]() -> Mask {cancel=true;throw std::runtime_error("interrupted");},[&]{return cancel;});}
    catch(const std::exception&) {}
    expect(cache.size()==1,"cancelled mask is not cached");++count;
    return count;
}
void command() {
    ACHAR path[4096]={};if(acedGetString(1,_T("\nClearance test request: "),path)!=RTNORM)return;
    std::string output;
    try {
        std::ifstream in(AcString(path).utf8Str());output=line(in);
        expect(!std::ifstream(output).good(),"output exists");
        const auto source=line(in);
        struct Context {
            AcDbHostApplicationServices* services=acdbHostApplicationServices();
            AcDbDatabase* previous=services->workingDatabase();
            AcDbField::EvalOption fields=acdbGlobalFieldEvaluationOption();
            std::unique_ptr<AcDbDatabase> database;
            ~Context() {
                services->setWorkingDatabase(previous);database.reset();
                acdbSetGlobalFieldEvaluationOption(fields);
            }
        } context;
        if(!source.empty()) {
            namespace fs=std::filesystem;
            const auto root=fs::canonical(AcString(path).utf8Str()).parent_path();
            expect(root.string().rfind("/private/tmp/ga-clearance-",0)==0
                &&fs::canonical(source).parent_path()==root,"private clearance copy required");
            expect(acdbSetGlobalFieldEvaluationOption(AcDbField::kDisable)==Acad::eOk,"field evaluation scope");
            context.database=std::make_unique<AcDbDatabase>(false,true);
            auto& db=*context.database;
            expect(db.dxfIn(AcString(source.c_str()).kwszPtr())==Acad::eOk,"side DXF read");
            context.services->setWorkingDatabase(&db);
            expect(acdbResolveCurrentXRefs(&db,false,false)==Acad::eOk,"side XREF resolve");
        }
        resbuf before{},after{};acedGetVar(_T("DBMOD"),&before);
        const auto start=std::chrono::steady_clock::now();
        const auto tests=controls();
        std::ostringstream out;out<<std::setprecision(17)<<"{\"controls_passed\":"<<tests;
        out<<",\"control_seconds\":"<<std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
        out<<",\"objects\":[";
        const auto count=std::stoi(line(in));expect(count>=0&&count<=4096,"object count");
        Cache cache;
        for(int i=0;i<count;++i) {
            const auto route=line(in),mode=line(in);const double distance=std::stod(line(in));
            out<<(i?",":"")<<"{\"route\":"<<q(route)<<",\"distance\":"<<distance;
            const auto t=std::chrono::steady_clock::now();
            try {
                ga::xref::Instance instance;ga::xref::resolve(*acdbHostApplicationServices()->workingDatabase(),route,instance);
                if(auto* arc=AcDbArc::cast(instance.entity)) {
                    out<<",\"arc_radius\":"<<arc->radius()<<",\"arc_start\":"<<arc->startAngle()<<",\"arc_end\":"<<arc->endAngle();
                }
                const ga::nativeQuery::ObjectTarget target{route,mode=="area"?ga::nativeQuery::QueryCapability::Area:ga::nativeQuery::QueryCapability::Curve};
                auto builder=[&]() {
                    if(mode=="area") {
                        ga::direct::Prepared owner;
                        auto* region=ga::direct::prepareLocalArea(instance.entity,owner);
                        return aroundArea(*region,instance.transform,distance);
                    }
                    expect(mode=="curve","unknown mode");
                    return aroundCurve(*instance.entity,instance.transform,distance);
                };
                const auto& entry=cache.get(target,distance,builder);
                if(!entry.error.empty()) throw std::runtime_error(entry.error);
                const auto& mask=entry.mask;
                out<<",\"area\":"<<(mask.region?area(*mask.region):0)<<",\"pieces\":"<<mask.boundaryPieces;
                out<<",\"build_seconds\":"<<std::chrono::duration<double>(std::chrono::steady_clock::now()-t).count();
                const auto repeatStart=std::chrono::steady_clock::now();
                expect(cache.get(target,distance,builder).hit,"real mask cache hit");
                out<<",\"cache_seconds\":"<<std::chrono::duration<double>(std::chrono::steady_clock::now()-repeatStart).count();
                ga::nativeQuery::AffineAreaQuery maskQuery;maskQuery.prepare(*mask.region,AcGeMatrix3d::kIdentity);
                std::unique_ptr<ga::nativeQuery::AffineAreaQuery> sourceArea;
                std::unique_ptr<ga::nativeQuery::CurveQuery> sourceCurve;
                if(mode=="area") {sourceArea=std::make_unique<ga::nativeQuery::AffineAreaQuery>();sourceArea->prepare(*instance.entity,instance.transform);}
                else {sourceCurve=std::make_unique<ga::nativeQuery::CurveQuery>();sourceCurve->prepare(*instance.entity,instance.transform);}
                const auto box=maskQuery.evidence();unsigned comparisons=0;
                for(unsigned x=0;x<9;++x) for(unsigned y=0;y<9;++y) {
                    const AcGePoint3d point(box.low.x+(box.high.x-box.low.x)*(x+.319)/9,box.low.y+(box.high.y-box.low.y)*(y+.127)/9,0);
                    const auto reference=sourceArea?sourceArea->queryPlanar(point):sourceCurve->queryPlanar(point);
                    const auto buffered=maskQuery.queryPlanar(point);
                    expect(reference.status==0&&reference.distanceComplete&&buffered.status==0,"real comparison available");
                    if(std::abs(reference.distance-distance)<kPlanarTolerance||buffered.membership=="edge") continue;
                    expect((reference.membership=="occupied"||reference.distance<distance)==(buffered.membership=="occupied"),"real mask and direct distance disagree");++comparisons;
                }
                out<<",\"distance_comparisons\":"<<comparisons;
            } catch(const std::exception& e) {out<<",\"error\":"<<q(e.what());}
            out<<",\"seconds\":"<<std::chrono::duration<double>(std::chrono::steady_clock::now()-t).count()<<'}';
        }
        acedGetVar(_T("DBMOD"),&after);expect(before.resval.rint==after.resval.rint,"source DBMOD changed");
        out<<"],\"dbmod_before\":"<<before.resval.rint<<",\"dbmod_after\":"<<after.resval.rint<<'}';
        expect(ga::bridge::writeAtomicText(output,out.str()),"publish");
    } catch(const std::exception& e) {
        if(!output.empty()) ga::bridge::writeAtomicText(output,"{\"error\":"+q(e.what())+"}");
        acutPrintf(_T("\nClearance controls failed: %s"),AcString(e.what()).kwszPtr());
    }
}
}
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode msg,void* id) {
    if(msg==AcRx::kInitAppMsg) {
        acrxLoadModule(_T("AcGeomentObj.dbx"),0);acrxDynamicLinker->loadModule(_T("AcBr.dbx"),1);
        acrxDynamicLinker->unlockApplication(id);acrxDynamicLinker->registerAppMDIAware(id);
        acedRegCmds->addCommand(_T("GA_CLEARANCE_TEST"),_T("GACLEARANCETEST"),_T("GACLEARANCETEST"),ACRX_CMD_MODAL,command);
    } else if(msg==AcRx::kUnloadAppMsg) acedRegCmds->removeGroup(_T("GA_CLEARANCE_TEST"));
    return AcRx::kRetOK;
}
