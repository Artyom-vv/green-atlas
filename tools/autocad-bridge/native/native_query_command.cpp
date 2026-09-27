#include "native_query_command.h"
#include "native_query_batch.h"
#include "native_area_group.h"
#include "bridge_config.h"
#include "file_io.h"
#include "aced.h"
#include "adslib.h"
#include "AcString.h"
#include <cmath>
#include <fstream>
#include <iomanip>
#include <locale>
#include <set>
#include <sstream>
#include <stdexcept>

namespace ga::nativeQuery {
namespace {
constexpr std::size_t kMaxRequestBytes=4*1024*1024;
const char* kProtocol="green-atlas.native-object-query/2";
std::string q(const std::string& s) { return "\""+ga::bridge::jsonEscape(s)+"\""; }
std::string line(std::istream& in) {
    std::string value;
    if(!std::getline(in,value)||value.empty()||value.size()>4096||value.find('\r')!=std::string::npos)
        throw std::runtime_error("invalid native query request line");
    return value;
}
bool hex(const std::string& value,std::size_t size) {
    return value.size()==size&&value.find_first_not_of("0123456789abcdef")==std::string::npos;
}
template<class... T> void numbers(const std::string& value,T&... fields) {
    std::istringstream row(value); row.imbue(std::locale::classic());
    if(!(row>>...>>fields)) throw std::runtime_error("invalid native query numbers");
    row>>std::ws;
    if(!row.eof()) throw std::runtime_error("extra native query fields");
}
struct Request {
    std::string output,id,sha;
    int units=0;
    std::vector<ObjectTarget> targets;
    std::vector<AcGePoint3d> points;
};
Request read(const std::string& path) {
    if(ga::bridge::fileSize(path)>kMaxRequestBytes) throw std::runtime_error("native query request too large");
    std::ifstream in(path); Request r;
    if(line(in)!=kProtocol) throw std::runtime_error("native query protocol mismatch");
    r.output=line(in); r.id=line(in); r.sha=line(in);
    if(r.output.front()!='/'||std::ifstream(r.output).good()||!hex(r.id,32)||!hex(r.sha,64))
        throw std::runtime_error("native query output/identity invalid");
    numbers(line(in),r.units);
    std::size_t objectCount=0,pointCount=0;
    numbers(line(in),objectCount,pointCount);
    if(!objectCount||objectCount>kBatchObjectLimit||!pointCount||pointCount>kBatchPointLimit
        ||objectCount*pointCount>kBatchMeasurementLimit) throw std::runtime_error("native query batch limits");
    for(std::size_t i=0;i<objectCount;++i) {
        ObjectTarget target; std::string mode;
        std::size_t memberCount=0;
        numbers(line(in),target.route,mode,memberCount);
        if(mode.rfind("face_",0)==0) {
            numbers(mode.substr(5),target.faceId);
            if(!target.faceId||memberCount) throw std::runtime_error("invalid derived face target");
            mode="area";
        }
        if(mode!="area"&&mode!="curve"&&mode!="closed_area") throw std::runtime_error("invalid native capability");
        if(memberCount>=kAreaGroupMemberLimit) throw std::runtime_error("native group member limit");
        for(std::size_t j=0;j<memberCount;++j) target.additionalRoutes.push_back(line(in));
        target.capability=mode=="closed_area"?QueryCapability::ClosedArea:
            mode=="area"?QueryCapability::Area:QueryCapability::Curve;
        r.targets.push_back(target);
    }
    for(std::size_t i=0;i<pointCount;++i) {
        AcGePoint3d point; numbers(line(in),point.x,point.y,point.z); r.points.push_back(point);
    }
    in>>std::ws;
    if(!in.eof()) throw std::runtime_error("extra native query content");
    return r;
}
void emit(std::ostream& out,const BatchMeasurements& batch) {
    out<<"[";
    for(std::size_t i=0;i<batch.objects.size();++i) {
        const auto& o=batch.objects[i];
        out<<(i?",":"")<<"{\"route\":"<<q(o.route)<<",\"entity_type\":"<<q(o.entityType)
            <<",\"layer\":"<<q(o.layer)<<",\"capability\":"<<q(o.capability)
            <<",\"interior_known\":"<<(o.interiorKnown?"true":"false")
            <<",\"preparation_error\":"<<q(o.preparationError)<<",\"prepare_ms\":"<<o.prepareMs
            <<",\"face_id\":"<<o.faceId<<",\"additional_routes\":[";
        for(std::size_t j=0;j<o.additionalRoutes.size();++j) out<<(j?",":"")<<q(o.additionalRoutes[j]);
        out<<"],\"answers\":[";
        for(std::size_t j=0;j<o.answers.size();++j) {
            const auto& a=o.answers[j];
            out<<(j?",":"")<<"{\"point_index\":"<<j<<",\"status\":"<<a.status
                <<",\"membership\":"<<q(a.membership)<<",\"distance_units\":";
            if(a.distanceComplete&&std::isfinite(a.distance)) out<<a.distance; else out<<"null";
            out<<",\"error\":"<<q(o.queryErrors[j])<<"}";
        }
        out<<"]}";
    }
    out<<"]";
}
}
void queryObjectsFile(const std::string& requestPath, const std::function<void()>& checkBasis) {
        if(checkBasis) checkBasis();
        const auto requestHash=ga::bridge::sha256File(requestPath);
        const auto r=read(requestPath);
        auto* db=acdbHostApplicationServices()->workingDatabase();
        const ACHAR* filename=nullptr;
        if(!db||db->getFilename(filename)!=Acad::eOk||!filename)
            throw std::runtime_error("native query has no loaded source");
        const std::string source=AcString(filename).utf8Str();
        if(ga::bridge::sha256File(source)!=r.sha||int(db->insunits())!=r.units)
            throw std::runtime_error("native source hash/units mismatch");
        resbuf modified{};
        if(acedGetVar(_T("DBMOD"),&modified)!=RTNORM||modified.restype!=RTSHORT)
            throw std::runtime_error("native source DBMOD unavailable");
        const int initialModified=modified.resval.rint;
        AcString revision; db->getVersionGuid(revision);
        auto batch=measureBatch(*db,r.targets,r.points,[]{return acedUsrBrk()!=0;});
        AcString after; db->getVersionGuid(after);
        if(db!=acdbHostApplicationServices()->workingDatabase()||ga::bridge::sha256File(source)!=r.sha
            ||ga::bridge::sha256File(requestPath)!=requestHash
            ||acedGetVar(_T("DBMOD"),&modified)!=RTNORM||modified.restype!=RTSHORT
            ||modified.resval.rint!=initialModified||revision!=after)
            throw std::runtime_error("native source changed during query");
        std::ostringstream out; out.imbue(std::locale::classic()); out<<std::setprecision(17);
        out<<"{\"schema\":"<<q(kProtocol)<<",\"scope\":\"selected_instance_measurements_only\""
            <<",\"request_id\":"<<q(r.id)<<",\"request_sha256\":"<<q(requestHash)
            <<",\"plugin_version\":"<<q(ga::bridge::kPluginVersion)<<",\"source_sha256\":"<<q(r.sha)
            <<",\"database_revision\":"<<q(revision.utf8Str())<<",\"database_modified_flags\":"<<initialModified
            <<",\"units_code\":"<<r.units<<",\"point_count\":"<<r.points.size()
            <<",\"elapsed_ms\":"<<batch.elapsedMs<<",\"objects\":";
        emit(out,batch); out<<"}";
        if(checkBasis) checkBasis(); // Do not publish measurements of a stale editor/XREF session.
        if(!ga::bridge::writeAtomicText(r.output,out.str())) throw std::runtime_error("native query publication failed");
}
void queryObjectsCommand() {
    ACHAR path[4096]={};
    if(acedGetString(1,_T("\nNative query request: "),path)!=RTNORM) return;
    try {
        queryObjectsFile(AcString(path).utf8Str());
        acutPrintf(_T("\nGreen Atlas: native object measurements written."));
    } catch(const std::exception& error) {
        acutPrintf(_T("\nGreen Atlas: native query failed: %s"),AcString(error.what()).kwszPtr());
    }
}
}
