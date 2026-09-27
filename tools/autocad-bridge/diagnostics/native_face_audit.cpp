// Experimental planar face walk over AutoCAD-owned curve fragments.
// All intersections, splitting, lengths, final regions and areas are native.
// No original object is edited, no result is admitted to a user project.
#include "rxregsvc.h"
#include "core_rxmfcapi.h"
#include "aced.h"
#include "adslib.h"
#include "AcString.h"
#include "dbcurve.h"
#include "dbsymtb.h"
#include "dbidmap.h"
#include "dbents.h"
#include "dbpl.h"
#include "native_area_group.h"
#include "xref_instance_access.h"
#include "file_io.h"
#include <algorithm>
#include <cmath>
#include <fstream>
#include <functional>
#include <iomanip>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>

namespace {
constexpr double kEquality=1e-8;
std::string q(const std::string& s){return "\""+ga::bridge::jsonEscape(s)+"\"";}
std::string line(std::istream& in){std::string s;if(!std::getline(in,s)||s.empty())throw std::runtime_error("request line");return s;}
void check(Acad::ErrorStatus s,const char* op){if(s!=Acad::eOk)throw std::runtime_error(std::string(op)+":"+std::to_string(int(s)));}
void point(std::ostream& out,const AcGePoint3d& p){out<<'['<<p.x<<','<<p.y<<','<<p.z<<']';}
struct Input {std::string route,layer;AcDbCurve* curve=nullptr;std::vector<double> cuts;double first=0,last=0;AcDbExtents bounds;};
struct Piece {AcDbCurve* curve;std::size_t source;int a,b;double first,last,length;std::set<std::size_t> sourceAliases;};
struct Graph {
    std::vector<AcGePoint3d> nodes;
    std::vector<std::vector<std::pair<double,int>>> outgoing;
    std::vector<Piece> pieces;
    unsigned coincidentSegments=0;
    int node(const AcGePoint3d& p){
        for(std::size_t i=0;i<nodes.size();++i)if(nodes[i].distanceTo(p)<=kEquality)return int(i);
        nodes.push_back(p);outgoing.emplace_back();return int(nodes.size()-1);
    }
    void add(AcDbCurve* c,std::size_t source){
        AcGePoint3d a,b;double first=0,last=0,d0=0,d1=0;
        check(c->getStartPoint(a),"piece start");check(c->getEndPoint(b),"piece end");
        check(c->getStartParam(first),"piece start parameter");check(c->getEndParam(last),"piece end parameter");
        check(c->getDistAtParam(first,d0),"piece start distance");check(c->getDistAtParam(last,d1),"piece end distance");
        if(d1-d0<=kEquality)return;
        AcGeVector3d da,db;check(c->getFirstDeriv(first,da),"start tangent");check(c->getFirstDeriv(last,db),"end tangent");
        const int ia=node(a),ib=node(b);
        // Coincident native LINE fragments have one geometric support even
        // when authored several times. Keep every source identity for review.
        if(AcDbLine::cast(c))for(auto& previous:pieces){
            if(!AcDbLine::cast(previous.curve)||std::abs(previous.length-(d1-d0))>kEquality)continue;
            if((previous.a==ia&&previous.b==ib)||(previous.a==ib&&previous.b==ia)){
                previous.sourceAliases.insert(source);++coincidentSegments;return;
            }
        }
        const int index=int(pieces.size());
        pieces.push_back({c,source,ia,ib,first,last,d1-d0,{source}});
        outgoing[ia].push_back({std::atan2(da.y,da.x),2*index});
        outgoing[ib].push_back({std::atan2(-db.y,-db.x),2*index+1});
    }
};
void cut(Input& input,const AcGePoint3d& p){
    AcGePoint3d nearest;if(input.curve->getClosestPointTo(p,nearest,false)!=Acad::eOk||p.distanceTo(nearest)>kEquality)return;
    double t=0;if(input.curve->getParamAtPoint(nearest,t)!=Acad::eOk)return;
    if(t<=input.first+kEquality||t>=input.last-kEquality)return;
    for(auto previous:input.cuts)if(std::abs(t-previous)<kEquality)return;
    input.cuts.push_back(t);
}
void command(){
    ACHAR path[4096]={};if(acedGetString(1,_T("\nFace audit request: "),path)!=RTNORM)return;
    try {
        std::ifstream in(AcString(path).utf8Str());const auto output=line(in);
        if(std::ifstream(output).good())throw std::runtime_error("output exists");
        const auto count=std::stoi(line(in));if(count<1||count>200)throw std::runtime_error("input budget");
        auto* host=acdbHostApplicationServices()->workingDatabase();resbuf before{};acedGetVar(_T("DBMOD"),&before);
        std::vector<std::unique_ptr<ga::xref::Instance>> sources;
        std::vector<Input> inputs;AcDbObjectIdArray ids;
        for(int i=0;i<count;++i){
            Input input;input.route=line(in);input.layer=line(in);auto item=std::make_unique<ga::xref::Instance>();
            ga::xref::resolve(*host,input.route,*item);
            if(!sources.empty()&&(item->entity->ownerId()!=sources.front()->entity->ownerId()
                ||input.route.substr(0,input.route.find_last_of('/'))!=inputs.front().route.substr(0,inputs.front().route.find_last_of('/'))))
                throw std::runtime_error("research input must be one definition/instance");
            ids.append(item->entity->objectId());sources.push_back(std::move(item));inputs.push_back(std::move(input));
        }
        AcDbDatabase scratch(true,true);ga::direct::ReadEntities opened;
        AcDbBlockTable* table=nullptr;check(scratch.getBlockTable(table,AcDb::kForRead),"scratch table");
        AcDbObjectId modelId;auto ms=table->getAt(ACDB_MODEL_SPACE,modelId);table->close();check(ms,"model ID");
        AcDbIdMapping mapping;check(host->wblockCloneObjects(ids,modelId,mapping,AcDb::kDrcIgnore,false),"clone to scratch");
        for(std::size_t i=0;i<inputs.size();++i){
            AcDbIdPair pair;pair.setKey(ids[int(i)]);if(!mapping.compute(pair)||!pair.isCloned())throw std::runtime_error("clone map");
            AcDbEntity* e=nullptr;check(acdbOpenObject(e,pair.value(),AcDb::kForRead),"read clone");opened.values.push_back(e);
            auto& input=inputs[i];input.curve=AcDbCurve::cast(e);if(!input.curve)throw std::runtime_error("not curve");
            check(input.curve->getStartParam(input.first),"first parameter");check(input.curve->getEndParam(input.last),"last parameter");
        }
        const bool explodeNative=line(in)=="1";
        const double connectorLimit=std::stod(line(in));
        if(!std::isfinite(connectorLimit)||connectorLimit<0||connectorLimit>.1)throw std::runtime_error("invalid diagnostic connector limit");
        // Optional, explicit research proposals. The authored curves stay intact.
        // This is a measured connector, not widening the exact-incidence tolerance.
        const auto connectorCount=std::stoi(line(in));
        std::vector<std::pair<std::string,double>> connectors;
        for(int connector=0;connector<connectorCount;++connector){
            const auto from=line(in),fromEnd=line(in),to=line(in),toEnd=line(in);
            auto a=std::find_if(inputs.begin(),inputs.end(),[&](const Input& v){return v.route==from;});
            auto b=std::find_if(inputs.begin(),inputs.end(),[&](const Input& v){return v.route==to;});
            if(a==inputs.end()||b==inputs.end())throw std::runtime_error("connector route missing");
            AcGePoint3d p,other;
            check(fromEnd=="start"?a->curve->getStartPoint(p):a->curve->getEndPoint(p),"connector start");
            if(toEnd=="nearest")check(b->curve->getClosestPointTo(p,other,false),"connector nearest");
            else check(toEnd=="start"?b->curve->getStartPoint(other):b->curve->getEndPoint(other),"connector target");
            const auto length=p.distanceTo(other);if(length<=kEquality||length>connectorLimit)throw std::runtime_error("connector outside declared diagnostic scope");
            auto* part=new AcDbLine(p,other);AcDbBlockTableRecord* model=nullptr;
            check(acdbOpenObject(model,modelId,AcDb::kForWrite),"connector model");AcDbObjectId id;
            const auto append=model->appendAcDbEntity(id,part);model->close();if(append!=Acad::eOk){delete part;check(append,"append connector");}part->close();
            AcDbEntity* read=nullptr;check(acdbOpenObject(read,id,AcDb::kForRead),"read connector");opened.values.push_back(read);
            Input input;input.route="connector:"+from+":"+fromEnd+"->"+to+":"+toEnd;input.layer="diagnostic connector";
            input.curve=AcDbCurve::cast(read);check(input.curve->getStartParam(input.first),"connector first");check(input.curve->getEndParam(input.last),"connector last");
            connectors.push_back({input.route,length});inputs.push_back(std::move(input));
        }
        if(explodeNative){
            std::vector<Input> expanded;
            for(auto& input:inputs){
                if(!AcDbPolyline::cast(input.curve)){expanded.push_back(input);continue;}
                AcDbVoidPtrArray parts;check(input.curve->explode(parts),"native explode polyline");
                for(void* raw:parts){
                    auto* part=static_cast<AcDbEntity*>(raw);AcDbBlockTableRecord* model=nullptr;
                    check(acdbOpenObject(model,modelId,AcDb::kForWrite),"explode model");AcDbObjectId id;
                    const auto append=model->appendAcDbEntity(id,part);model->close();if(append!=Acad::eOk){delete part;check(append,"append native segment");}part->close();
                    AcDbEntity* read=nullptr;check(acdbOpenObject(read,id,AcDb::kForRead),"read native segment");opened.values.push_back(read);
                    Input segment;segment.route=input.route;segment.layer=input.layer;segment.curve=AcDbCurve::cast(read);
                    if(!segment.curve)throw std::runtime_error("native segment not curve");
                    check(segment.curve->getStartParam(segment.first),"native segment first");check(segment.curve->getEndParam(segment.last),"native segment last");
                    double d0=0,d1=0;check(segment.curve->getDistAtParam(segment.first,d0),"segment d0");check(segment.curve->getDistAtParam(segment.last,d1),"segment d1");
                    if(d1-d0>kEquality)expanded.push_back(std::move(segment));
                }
            }
            inputs=std::move(expanded);
        }
        for(auto& input:inputs)check(input.curve->getGeomExtents(input.bounds),"input extents");
        unsigned intersections=0,failedIntersections=0;
        for(std::size_t i=0;i<inputs.size();++i)for(std::size_t j=0;j<i;++j){
            const auto& a=inputs[i].bounds;const auto& b=inputs[j].bounds;
            if(a.minPoint().x>b.maxPoint().x+kEquality||a.maxPoint().x<b.minPoint().x-kEquality
                ||a.minPoint().y>b.maxPoint().y+kEquality||a.maxPoint().y<b.minPoint().y-kEquality)continue;
            AcGePoint3dArray hits;const auto status=inputs[i].curve->intersectWith(inputs[j].curve,AcDb::kOnBothOperands,hits);
            if(status!=Acad::eOk){++failedIntersections;continue;}
            intersections+=hits.length();for(const auto& p:hits){cut(inputs[i],p);cut(inputs[j],p);}
            // Endpoint incidence covers small floating-point residuals without moving a vertex.
            for(auto* input:{&inputs[i],&inputs[j]}){
                auto& other=input==&inputs[i]?inputs[j]:inputs[i];AcGePoint3d a,b;
                check(input->curve->getStartPoint(a),"start incidence");check(input->curve->getEndPoint(b),"end incidence");
                cut(other,a);cut(other,b);
            }
        }
        Graph graph;std::vector<std::string> splitErrors;
        for(std::size_t i=0;i<inputs.size();++i){
            auto& input=inputs[i];std::sort(input.cuts.begin(),input.cuts.end());
            if(input.cuts.empty()){graph.add(input.curve,i);continue;}
            AcGeDoubleArray parameters;for(auto t:input.cuts)parameters.append(t);
            AcDbVoidPtrArray parts;const auto status=input.curve->getSplitCurves(parameters,parts);
            if(status!=Acad::eOk){for(void* raw:parts)delete static_cast<AcDbEntity*>(raw);splitErrors.push_back(input.route+":"+std::to_string(int(status)));continue;}
            for(void* raw:parts){
                auto* part=static_cast<AcDbEntity*>(raw);AcDbBlockTableRecord* model=nullptr;
                check(acdbOpenObject(model,modelId,AcDb::kForWrite),"write scratch model");
                AcDbObjectId id;const auto append=model->appendAcDbEntity(id,part);model->close();
                if(append!=Acad::eOk){delete part;check(append,"append fragment");}part->close();
                AcDbEntity* read=nullptr;check(acdbOpenObject(read,id,AcDb::kForRead),"read fragment");opened.values.push_back(read);
                auto* curve=AcDbCurve::cast(read);if(!curve)throw std::runtime_error("split returned noncurve");graph.add(curve,i);
            }
        }
        for(auto& list:graph.outgoing)std::sort(list.begin(),list.end());
        // A dangling detail inside a valid face must not make the face walk
        // reject the entire building when it traverses that detail out-and-back.
        std::vector<int> discovery(graph.nodes.size(),-1),low(graph.nodes.size());std::set<int> bridges;int timer=0;
        std::function<void(int,int)> visit=[&](int node,int parentEdge){
            discovery[node]=low[node]=timer++;
            for(const auto& item:graph.outgoing[node]){
                const int edge=item.second/2;if(edge==parentEdge)continue;const auto& p=graph.pieces[edge];
                const int neighbor=(item.second%2)?p.a:p.b;
                if(discovery[neighbor]<0){visit(neighbor,edge);low[node]=std::min(low[node],low[neighbor]);if(low[neighbor]>discovery[node])bridges.insert(edge);}
                else low[node]=std::min(low[node],discovery[neighbor]);
            }
        };
        for(int i=0;i<int(graph.nodes.size());++i)if(discovery[i]<0)visit(i,-1);
        for(auto& list:graph.outgoing)list.erase(std::remove_if(list.begin(),list.end(),[&](const auto& item){return bridges.count(item.second/2);}),list.end());
        std::ostringstream out;out<<std::setprecision(17)<<"{\"scope\":\"native exact face proposals; not semantic acceptance\",\"intersections\":"<<intersections
            <<",\"native_exploded\":"<<(explodeNative?"true":"false")<<",\"native_input_count\":"<<inputs.size()
            <<",\"coincident_native_segments\":"<<graph.coincidentSegments
            <<",\"connector_limit\":"<<connectorLimit
            <<",\"failed_intersections\":"<<failedIntersections<<",\"dangling_piece_count\":"<<bridges.size()<<",\"connectors\":[";
        for(std::size_t i=0;i<connectors.size();++i)out<<(i?",":"")<<"{\"route\":"<<q(connectors[i].first)<<",\"length\":"<<connectors[i].second<<'}';
        out<<"],\"split_errors\":[";
        for(std::size_t i=0;i<splitErrors.size();++i)out<<(i?",":"")<<q(splitErrors[i]);
        out<<"],\"pieces\":[";
        for(std::size_t i=0;i<graph.pieces.size();++i){
            auto& p=graph.pieces[i];out<<(i?",":"")<<"{\"route\":"<<q(inputs[p.source].route)<<",\"layer\":"<<q(inputs[p.source].layer)<<",\"source_routes\":[";
            bool firstSource=true;std::set<std::string> routes;for(auto alias:p.sourceAliases)routes.insert(inputs[alias].route);
            for(const auto& route:routes){out<<(firstSource?"":",")<<q(route);firstSource=false;}
            out<<"],\"length\":"<<p.length<<",\"nodes\":["<<p.a<<','<<p.b<<"],\"display_points\":[";
            // These samples are ONLY the diagnostic illustration, never face construction.
            constexpr int displaySamples=64;double initial=0;check(p.curve->getDistAtParam(p.first,initial),"display origin");
            for(int j=0;j<=displaySamples;++j){AcGePoint3d pt;check(p.curve->getPointAtDist(initial+p.length*j/displaySamples,pt),"display point");out<<(j?",":"");point(out,pt);}
            out<<"]}";
        }
        out<<"],\"faces\":[";bool firstFace=true;std::set<int> visited;std::set<std::vector<int>> unique;
        for(int start=0;start<int(graph.pieces.size()*2);++start){
            if(visited.count(start)||bridges.count(start/2))continue;std::vector<int> directed;int next=start;
            while(!visited.count(next)){
                visited.insert(next);directed.push_back(next);const auto& p=graph.pieces[next/2];
                const int destination=(next%2)?p.a:p.b;const auto& list=graph.outgoing[destination];
                auto it=std::find_if(list.begin(),list.end(),[&](const auto& v){return v.second==(next^1);});
                if(it==list.end())throw std::runtime_error("missing reverse half edge");
                const auto index=std::size_t(it-list.begin());next=list[(index+list.size()-1)%list.size()].second;
            }
            std::vector<int> key;for(auto index:directed)key.push_back(index/2);std::sort(key.begin(),key.end());
            if(next!=start||std::adjacent_find(key.begin(),key.end())!=key.end()||!unique.insert(key).second)continue;
            out<<(firstFace?"":",")<<"{\"pieces\":[";firstFace=false;
            for(std::size_t i=0;i<directed.size();++i)out<<(i?",":"")<<directed[i]/2;
            out<<']';
            try {
                std::vector<AcDbEntity*> curves;for(auto index:directed)curves.push_back(graph.pieces[index/2].curve);
                ga::nativeQuery::AreaGroupQuery group;group.prepare(curves,sources.front()->transform);
                const auto& evidence=group.evidence();out<<",\"area\":"<<evidence.localArea*evidence.jacobian
                    <<",\"native_edges\":"<<evidence.edgeCount<<",\"bounds\":[";point(out,evidence.low);out<<',';point(out,evidence.high);out<<']';
                const std::vector<std::pair<std::string,AcGePoint3d>> controls={
                    {"long_chain_inside",{15986,-4975,0}},
                    {"clipped_building_inside",{15994.606306,-5245.249309,0}},
                    {"AE50_inside",{16087,-5231,0}},
                    {"outside_control",{15610,-5000,0}}
                };
                out<<",\"controls\":[";
                for(std::size_t c=0;c<controls.size();++c){
                    const auto answer=group.queryPlanar(controls[c].second);
                    out<<(c?",":"")<<"{\"label\":"<<q(controls[c].first)<<",\"status\":"<<answer.status
                        <<",\"membership\":"<<q(answer.membership)<<",\"distance\":"<<answer.distance<<'}';
                }
                out<<']';
            }catch(const std::exception& error){out<<",\"error\":"<<q(error.what());}
            out<<'}';
        }
        resbuf after{};acedGetVar(_T("DBMOD"),&after);out<<"],\"dbmod_before\":"<<before.resval.rint<<",\"dbmod_after\":"<<after.resval.rint<<'}';
        if(before.resval.rint!=after.resval.rint)throw std::runtime_error("source DBMOD changed");
        if(!ga::bridge::writeAtomicText(output,out.str()))throw std::runtime_error("publish");
        acutPrintf(_T("\nNative face audit completed."));
    }catch(const std::exception& e){acutPrintf(_T("\nNative face audit failed: %s"),AcString(e.what()).kwszPtr());}
}
}
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode msg,void* id){
    if(msg==AcRx::kInitAppMsg){
        acrxLoadModule(_T("AcGeomentObj.dbx"),0);acrxDynamicLinker->loadModule(_T("AcBr.dbx"),1);
        acrxDynamicLinker->unlockApplication(id);acrxDynamicLinker->registerAppMDIAware(id);
        acedRegCmds->addCommand(_T("GA_FACE_AUDIT"),_T("GAFACEAUDIT"),_T("GAFACEAUDIT"),ACRX_CMD_MODAL,command);
    }else if(msg==AcRx::kUnloadAppMsg)acedRegCmds->removeGroup(_T("GA_FACE_AUDIT"));
    return AcRx::kRetOK;
}
