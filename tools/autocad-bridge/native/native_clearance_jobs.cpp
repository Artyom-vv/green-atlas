#include "native_clearance_jobs.h"
#include "surface_extraction.h"
#include "file_io.h"
#include "aced.h"
#include "adslib.h"
#include <fstream>
#include <iomanip>
#include <locale>
#include <map>
#include <sstream>
#include <stdexcept>
#include <unistd.h>

namespace ga::clearance {
namespace {
constexpr std::size_t kBatchLimit=32,kJobLimit=8,kRequestBytes=8*1024*1024;
constexpr const char* kProtocol="green-atlas.clearance-domain/1";
struct Job {std::size_t total;double displayTolerance;std::unique_ptr<Domain> domain;};
std::map<std::string,Job> jobs;
std::list<std::string> recent;
Cache masks;
AcDbDatabase* owner=nullptr;
std::string capture;
std::string q(const std::string& s) {return "\""+ga::bridge::jsonEscape(s)+"\"";}
std::string line(std::istream& in) {
    std::string s;if(!std::getline(in,s)||s.empty()||s.size()>4096||s.find('\r')!=std::string::npos)
        throw std::runtime_error("invalid clearance request line");return s;
}
template<class... T> void numbers(const std::string& s,T&... values) {
    std::istringstream in(s);in.imbue(std::locale::classic());
    if(!(in>>...>>values)) throw std::runtime_error("invalid clearance request values");
    in>>std::ws;if(!in.eof()) throw std::runtime_error("extra clearance request values");
}
bool hex(const std::string& s,std::size_t n) {return s.size()==n&&s.find_first_not_of("0123456789abcdef")==std::string::npos;}
Region workRegion(std::istream& in) {
    unsigned polygons=0;numbers(line(in),polygons);
    if(!polygons||polygons>128) throw std::runtime_error("work polygon count");
    Region result;std::size_t vertices=0;
    for(unsigned p=0;p<polygons;++p) {
        unsigned rings=0;numbers(line(in),rings);
        if(!rings||rings>256) throw std::runtime_error("work ring count");
        Region part;
        for(unsigned r=0;r<rings;++r) {
            unsigned n=0;numbers(line(in),n);vertices+=n;
            if(n<3||vertices>65536) throw std::runtime_error("work vertex limit");
            std::vector<AcGePoint3d> points;
            for(unsigned i=0;i<n;++i) {AcGePoint3d point;numbers(line(in),point.x,point.y);points.push_back(point);}
            auto ring=polygon(points);
            if(!r) part=std::move(ring);
            else if(part) part=subtract(*part,*ring);
        }
        if(!part) throw std::runtime_error("empty work polygon");
        if(!result) result=std::move(part);
        else if(result->booleanOper(AcDb::kBoolUnite,part.get())!=Acad::eOk) throw std::runtime_error("work polygon union failed");
    }
    return result;
}
Constraint constraint(std::istream& in) {
    Constraint c;std::string mode,role;unsigned members=0,bounded=0,failed=0;
    numbers(line(in),c.target.route,mode,c.target.faceId,members,role,c.distance,c.reviewDistance,bounded,failed);
    if(mode!="area"&&mode!="curve"&&mode!="closed_area") throw std::runtime_error("invalid mask capability");
    c.target.capability=mode=="area"?ga::nativeQuery::QueryCapability::Area:
        mode=="curve"?ga::nativeQuery::QueryCapability::Curve:ga::nativeQuery::QueryCapability::ClosedArea;
    if(members>255||bounded>1||failed>1||!std::isfinite(c.distance)||c.distance<0
        ||!std::isfinite(c.reviewDistance)||c.reviewDistance<=0) throw std::runtime_error("invalid mask parameters");
    if(c.target.faceId&&(members||mode!="area")) throw std::runtime_error("invalid mask face");
    for(unsigned i=0;i<members;++i) c.target.additionalRoutes.push_back(line(in));
    if(role=="site") {c.participation=Participation::Site;if(c.distance!=0||mode=="curve") throw std::runtime_error("site must be area without offset");}
    else if(role=="review") c.participation=Participation::Review;
    else if(role!="obstacle") throw std::runtime_error("invalid mask participation");
    if(bounded) {
        std::array<double,4> b;numbers(line(in),b[0],b[1],b[2],b[3]);
        if(std::any_of(b.begin(),b.end(),[](double v){return !std::isfinite(v);})||b[0]>b[2]||b[1]>b[3]) throw std::runtime_error("invalid mask bounds");
        c.bounds=b;
    }
    if(failed) c.sourceError=line(in);
    return c;
}
void geometry(std::ostream& out,const AcDbRegion* region,double tolerance) {
    if(!region) {out<<"{\"type\":\"MultiPolygon\",\"coordinates\":[]}";return;}
    auto copy=cloneRegion(*region);ga::bridge::RegionTopology topology;
    if(!ga::bridge::extractRegionTopology(copy.get(),AcGeMatrix3d::kIdentity,tolerance,topology))
        throw std::runtime_error("clearance display export status "+std::to_string(topology.errorStatus));
    std::map<std::size_t,std::vector<const ga::bridge::RegionLoop*>> faces;
    for(const auto& loop:topology.loops) faces[loop.faceIndex].push_back(&loop);
    out<<"{\"type\":\"MultiPolygon\",\"coordinates\":[";bool firstFace=true;
    for(auto& [id,loops]:faces) {
        std::stable_sort(loops.begin(),loops.end(),[](auto* a,auto* b){return a->role=="outer"&&b->role!="outer";});
        out<<(firstFace?"":",")<<'[';firstFace=false;
        for(std::size_t i=0;i<loops.size();++i) {
            out<<(i?",":"")<<'[';const auto& points=loops[i]->coordinates;
            for(std::size_t j=0;j<points.size();++j) out<<(j?",":"")<<'['<<points[j].x<<','<<points[j].y<<']';
            out<<']';
        }
        out<<']';
    }
    out<<"]}";
}
}
void clearJobs() {jobs.clear();recent.clear();masks.clear();owner=nullptr;capture.clear();}
void advanceFile(AcDbDatabase& host,const std::string& sessionId,const std::string& path,
                 const std::string& output,const std::function<void()>& checkBasis) {
    checkBasis();
    if(owner&&(&host!=owner||sessionId!=capture)) clearJobs();
    owner=&host;capture=sessionId;
    if(ga::bridge::fileSize(path)>kRequestBytes||std::ifstream(output).good()) throw std::runtime_error("invalid clearance request size/output");
    const auto hash=ga::bridge::sha256File(path);
    std::ifstream in(path);if(line(in)!=kProtocol) throw std::runtime_error("clearance protocol mismatch");
    const auto id=line(in),sourceHash=line(in);
    if(!hex(id,64)||!hex(sourceHash,64)) throw std::runtime_error("invalid clearance identity");
    unsigned units=0;numbers(line(in),units);
    const ACHAR* filename=nullptr;
    if(host.getFilename(filename)!=Acad::eOk||!filename||sourceHash!=ga::bridge::sha256File(AcString(filename).utf8Str())||units!=unsigned(host.insunits()))
        throw std::runtime_error("clearance source/units mismatch");
    std::size_t offset=0,total=0,count=0;double tolerance=0;
    numbers(line(in),offset,total,count,tolerance);
    if(total>1000000||count>kBatchLimit||offset+count>total||!std::isfinite(tolerance)||tolerance<=0)
        throw std::runtime_error("invalid clearance batch limits");
    auto work=workRegion(in);std::vector<Constraint> batch;
    for(std::size_t i=0;i<count;++i) batch.push_back(constraint(in));
    in>>std::ws;if(!in.eof()) throw std::runtime_error("extra clearance request data");
    auto found=jobs.find(id);
    std::unique_ptr<Domain> next;
    if(found==jobs.end()) {
        if(offset) throw std::runtime_error("clearance_job_expired");
        next=std::make_unique<Domain>(*work);
    } else {
        if(found->second.total!=total||found->second.displayTolerance!=tolerance
            ||found->second.domain->progress.processed!=offset||found->second.domain->complete)
            throw std::runtime_error("clearance_job_sequence_mismatch");
        next=std::make_unique<Domain>(*found->second.domain);
    }
    const auto slash=path.find_last_of('/');
    const auto cancel=path.substr(0,slash+1)+"cancel-"+path.substr(slash+7,32);
    next->advance(host,masks,batch,offset+count==total,[&]{return acedUsrBrk()!=0||access(cancel.c_str(),F_OK)==0;});
    checkBasis();if(hash!=ga::bridge::sha256File(path)) throw std::runtime_error("clearance request changed");
    std::ostringstream out;out.imbue(std::locale::classic());out<<std::setprecision(17);
    out<<"{\"schema\":"<<q(kProtocol)<<",\"session_id\":"<<q(sessionId)<<",\"job_id\":"<<q(id)
       <<",\"request_sha256\":"<<q(hash)<<",\"processed\":"<<next->progress.processed<<",\"total\":"<<total
       <<",\"cache_hits\":"<<next->progress.cacheHits<<",\"elapsed_s\":"<<next->progress.elapsedSeconds
       <<",\"complete\":"<<(next->complete?"true":"false")<<",\"available_area\":"<<next->availableArea()
       <<",\"excluded_area\":"<<next->excludedArea()<<",\"unresolved_area\":"<<next->unresolvedArea()
       <<",\"work_area\":"<<next->workArea<<",\"geometry\":";
    geometry(out,next->availableRegion(),tolerance);
    out<<",\"unresolved_geometry\":";auto unknown=next->unresolvedRegion();geometry(out,unknown.get(),tolerance);
    out<<",\"issues\":[";
    for(std::size_t i=0;i<next->issues.size();++i) {
        const auto& issue=next->issues[i];out<<(i?",":"")<<"{\"route\":"<<q(issue.route)<<",\"face_id\":"<<issue.faceId
            <<",\"reason\":"<<q(issue.reason)<<",\"localized\":"<<(issue.localized?"true":"false")<<'}';
    }
    out<<"]}";checkBasis();
    if(!ga::bridge::writeAtomicText(output,out.str())) throw std::runtime_error("clearance publication failed");
    recent.remove(id);recent.push_back(id);
    jobs.insert_or_assign(id,Job{total,tolerance,std::move(next)});
    while(jobs.size()>kJobLimit) {jobs.erase(recent.front());recent.pop_front();}
}
}
