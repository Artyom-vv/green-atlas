// Calls the production face builder on controls and a same-capture source set.
#include "rxregsvc.h"
#include "core_rxmfcapi.h"
#include "aced.h"
#include "adslib.h"
#include "AcString.h"
#include "planar_faces.h"
#include "xref_instance_access.h"
#include "file_io.h"
#include "dbents.h"
#include "dbpl.h"
#include "dbhatch.h"
#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace {
std::string q(const std::string& s){return "\""+ga::bridge::jsonEscape(s)+"\"";}
std::string line(std::istream& in){std::string s;if(!std::getline(in,s)||s.empty())throw std::runtime_error("request line");return s;}
void expect(bool ok,const char* message){if(!ok)throw std::runtime_error(message);}
void add(std::vector<ga::faces::Source>& sources,std::string route,double x1,double y1,double x2,double y2,bool clipping=false) {
    sources.push_back({route,clipping?"cut":"physical",std::make_unique<AcDbLine>(AcGePoint3d(x1,y1,0),AcGePoint3d(x2,y2,0)),clipping});
}
unsigned selftest() {
    unsigned count=0;
    for(const double scale:{1.,1000.}) {
        for(const double gap:{0.,.01,.03}) {
            std::vector<ga::faces::Source> sources;
            add(sources,"1",0,0,0,10*scale);
            add(sources,"2",0,10*scale,10*scale,10*scale);
            add(sources,"3",10*scale,10*scale,10*scale,gap*scale);
            add(sources,"4",-5*scale,0,15*scale,0,true);
            auto result=ga::faces::assemble(std::move(sources),scale);
            expect(result.faces.size()==(gap<=.02?1:0),"bounded face/metric gap control");
            if(!result.faces.empty()) {
                expect(std::abs(result.faces[0].query->evidence().localArea-100*scale*scale)<.001,"area control");
                expect(result.faces[0].query->queryPlanar({5*scale,5*scale,0}).membership=="occupied","inside control");
                expect(result.faces[0].query->queryPlanar({20*scale,5*scale,0}).membership=="outside","outside control");
            }
            ++count;
        }
    }
    std::vector<ga::faces::Source> sources;
    auto poly=std::make_unique<AcDbPolyline>();
    const std::vector<AcGePoint2d> points={{0,0},{10,0},{10,10},{0,10},{0,0},{-1,0}};
    for(unsigned i=0;i<points.size();++i) poly->addVertexAt(i,points[i]);
    sources.push_back({"1","building",std::move(poly),false});
    auto tail=ga::faces::assemble(std::move(sources),1);
    expect(tail.faces.size()==1&&std::abs(tail.faces[0].query->evidence().localArea-100)<1e-8,"loop with tail control");
    return count+1;
}
void command() {
    ACHAR path[4096]={};if(acedGetString(1,_T("\nProduction face test request: "),path)!=RTNORM)return;
    try {
        std::ifstream in(AcString(path).utf8Str());const auto output=line(in);
        expect(!std::ifstream(output).good(),"output exists");
        const auto count=std::stoi(line(in));expect(count>0&&count<10000,"input count");
        auto* host=acdbHostApplicationServices()->workingDatabase();
        resbuf before{},after{};acedGetVar(_T("DBMOD"),&before);
        std::vector<ga::faces::Source> sources;
        for(int i=0;i<count;++i) {
            const auto route=line(in),layer=line(in);
            ga::xref::Instance instance;ga::xref::resolve(*host,route,instance);
            auto* curve=AcDbCurve::cast(instance.entity);expect(curve,"input is curve");
            AcDbEntity* transformed=nullptr;
            expect(curve->getTransformedCopy(instance.transform,transformed)==Acad::eOk,"world clone");
            auto* world=AcDbCurve::cast(transformed);expect(world,"world curve");
            sources.push_back({route,layer,std::unique_ptr<AcDbCurve>(world),layer.find("Граница заказа")!=std::string::npos});
        }
        std::ostringstream out;out<<std::setprecision(17)<<"{\"selftests\":"<<selftest();
        auto result=ga::faces::assemble(std::move(sources),1);
        out<<",\"issues\":[";
        for(std::size_t i=0;i<result.issues.size();++i)out<<(i?",":"")<<q(result.issues[i]);
        out<<"],\"faces\":[";
        for(std::size_t i=0;i<result.faces.size();++i) {
            const auto& face=result.faces[i];out<<(i?",":"")<<"{\"area\":"<<face.query->evidence().localArea<<",\"routes\":[";
            bool first=true;for(const auto& route:face.routes){out<<(first?"":",")<<q(route);first=false;}
            out<<"],\"repairs\":"<<face.repairs.size()<<",\"controls\":[";
            const std::vector<AcGePoint3d> controls={{15986,-4975,0},{15994.606306,-5245.249309,0},{16087,-5231,0},{15610,-5000,0}};
            for(std::size_t c=0;c<controls.size();++c)out<<(c?",":"")<<q(face.query->queryPlanar(controls[c]).membership);
            out<<"]}";
        }
        out<<"],\"hatch_clone_checks\":[";
        for(unsigned i=0;i<2;++i) {
            const std::string route=i?"6DE6/2DCAD9":"6DE6/2918D5";
            ga::xref::Instance instance;ga::xref::resolve(*host,route,instance);
            auto* original=AcDbHatch::cast(instance.entity);expect(original,"hatch source");
            std::unique_ptr<AcDbHatch> clone(AcDbHatch::cast(original->clone()));expect(bool(clone),"hatch clone");
            const auto status=clone->evaluateHatch();
            std::unique_ptr<AcDbRegion> region(clone->getRegionArea());
            out<<(i?",":"")<<"{\"route\":"<<q(route)<<",\"evaluate_status\":"<<int(status)<<",\"region\":"<<(region?"true":"false")<<'}';
        }
        acedGetVar(_T("DBMOD"),&after);expect(before.resval.rint==after.resval.rint,"source DBMOD changed");
        out<<"],\"dbmod_before\":"<<before.resval.rint<<",\"dbmod_after\":"<<after.resval.rint<<'}';
        expect(ga::bridge::writeAtomicText(output,out.str()),"publish");
        acutPrintf(_T("\nProduction face pipeline controls completed."));
    } catch(const std::exception& e) {acutPrintf(_T("\nProduction face controls failed: %s"),AcString(e.what()).kwszPtr());}
}
}
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode msg,void* id) {
    if(msg==AcRx::kInitAppMsg) {
        acrxLoadModule(_T("AcGeomentObj.dbx"),0);acrxDynamicLinker->loadModule(_T("AcBr.dbx"),1);
        acrxDynamicLinker->unlockApplication(id);acrxDynamicLinker->registerAppMDIAware(id);
        acedRegCmds->addCommand(_T("GA_FACE_PIPELINE_TEST"),_T("GAFACEAUDIT"),_T("GAFACEAUDIT"),ACRX_CMD_MODAL,command);
    } else if(msg==AcRx::kUnloadAppMsg) acedRegCmds->removeGroup(_T("GA_FACE_PIPELINE_TEST"));
    return AcRx::kRetOK;
}
