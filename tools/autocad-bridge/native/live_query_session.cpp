#include "live_query_session.h"
#include "live_inventory.h"
#include "native_face_catalog.h"
#include "native_query_command.h"
#include "capture_commands.h"
#include "file_io.h"
#include "bridge_config.h"
#include "acdocman.h"
#include "dbapserv.h"
#include "acdbxref.h"
#include "xgraph.h"
#include "AcString.h"
#include "adslib.h"
#include <algorithm>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>
#include <fstream>
#include <iomanip>
#include <unistd.h>

namespace ga::liveQuery {
namespace {
std::string q(const std::string& s) { return "\""+ga::bridge::jsonEscape(s)+"\""; }
class Session final : public AcDbDatabaseReactor {
public:
    AcApDocument* document = nullptr;
    AcDbDatabase* host = nullptr;
    std::set<AcDbDatabase*> watched;
    std::string token, source, sourceHash, snapshot, snapshotHash, inventory, inventoryHash;
    bool changed = false;
    unsigned missingXrefs = 0;
    ~Session() override { for(auto* db:watched) db->removeReactor(this); }
    void objectAppended(const AcDbDatabase*, const AcDbObject*) override { changed=true; }
    void objectUnAppended(const AcDbDatabase*, const AcDbObject*) override { changed=true; }
    void objectReAppended(const AcDbDatabase*, const AcDbObject*) override { changed=true; }
    void objectModified(const AcDbDatabase*, const AcDbObject*) override { changed=true; }
    void objectErased(const AcDbDatabase*, const AcDbObject*, bool) override { changed=true; }
    void headerSysVarChanged(const AcDbDatabase*, const ACHAR* name, bool success) override {
        // Pan/zoom is not an edit of the calculation basis; unit changes are.
        if(success && AcString(name)==_T("INSUNITS")) changed=true;
    }
    void goodbye(const AcDbDatabase* db) override {
        changed=true; watched.erase(const_cast<AcDbDatabase*>(db));
    }
    void watch(AcDbDatabase* db) {
        if(db && watched.insert(db).second) db->addReactor(this);
    }
    void check() const {
        auto* current=acDocManager?acDocManager->curDocument():nullptr;
        if(changed || !current || current!=document || current->database()!=host
            || acdbHostApplicationServices()->workingDatabase()!=host)
            throw std::runtime_error("live_source_changed_or_inactive");
        const ACHAR* filename=nullptr;
        if(host->getFilename(filename)!=Acad::eOk || !filename
            || source!=AcString(filename).utf8Str()
            || ga::bridge::sha256File(source)!=sourceHash)
            throw std::runtime_error("live_source_file_changed");
        AcDbXrefGraph graph;
        if(acdbGetHostDwgXrefGraph(host,graph,Adesk::kTrue)!=Acad::eOk)
            throw std::runtime_error("live_xref_graph_unavailable");
        std::set<AcDbDatabase*> currentDatabases{host};
        unsigned missing=0;
        for(int i=1;i<graph.numNodes();++i) {
            auto* node=graph.xrefNode(i);
            if(node && node->xrefStatus()==AcDb::kXrfResolved && node->database())
                currentDatabases.insert(node->database());
            else ++missing;
        }
        if(currentDatabases!=watched || missing!=missingXrefs)
            throw std::runtime_error("live_xref_graph_changed");
    }
    std::string metadata() const {
        std::ostringstream out;
        out<<"{\"session_id\":"<<q(token)<<",\"pid\":"<<getpid()
            <<",\"plugin_version\":"<<q(ga::bridge::kPluginVersion)
            <<",\"source_path\":"<<q(source)<<",\"source_sha256\":"<<q(sourceHash)
            <<",\"snapshot_path\":"<<q(snapshot)<<",\"snapshot_sha256\":"<<q(snapshotHash)
            <<",\"inventory_path\":"<<q(inventory)<<",\"inventory_sha256\":"<<q(inventoryHash)
            <<",\"units_code\":"<<int(host->insunits())
            <<",\"watched_databases\":"<<watched.size()
            <<",\"unavailable_xrefs\":"<<missingXrefs<<"}";
        return out.str();
    }
};
std::unique_ptr<Session> session;
Session& checked(const std::string& token) {
    if(!session || session->token!=token) throw std::runtime_error("live_session_expired");
    session->check(); return *session;
}
}
void closeSession() { ga::faces::clearCatalog(); session.reset(); }
std::string openSession(const std::string& token, const std::string& snapshotPath) {
    if(token.size()!=32 || token.find_first_not_of("0123456789abcdef")!=std::string::npos)
        throw std::runtime_error("live_session_invalid_token");
    auto* document=acDocManager?acDocManager->curDocument():nullptr;
    if(!document || document->database()!=acdbHostApplicationServices()->workingDatabase())
        throw std::runtime_error("live_document_unavailable");
    auto next=std::make_unique<Session>();
    next->document=document; next->host=document->database(); next->token=token;
    const ACHAR* filename=nullptr;
    if(next->host->getFilename(filename)!=Acad::eOk || !filename)
        throw std::runtime_error("live_source_filename_unavailable");
    next->source=AcString(filename).utf8Str();
    next->sourceHash=ga::bridge::sha256File(next->source);
    if(next->sourceHash.empty()) throw std::runtime_error("live_source_file_unavailable");
    next->snapshot=snapshotPath;
    next->inventory=snapshotPath+".inventory.json";
    AcDbXrefGraph graph;
    if(acdbGetHostDwgXrefGraph(next->host,graph,Adesk::kTrue)!=Acad::eOk)
        throw std::runtime_error("live_xref_graph_unavailable");
    next->watch(next->host);
    for(int i=1;i<graph.numNodes();++i) {
        auto* node=graph.xrefNode(i);
        if(node && node->xrefStatus()==AcDb::kXrfResolved && node->database())
            next->watch(node->database());
        else ++next->missingXrefs;
    }
    std::string error;
    if(!ga::bridge::captureLiveSnapshotForService(snapshotPath,error,[&](const auto& drawing) {
        writeInventory(*next->host,drawing,next->inventory);
    }))
        throw std::runtime_error("live_capture_failed: "+error);
    next->check();
    next->snapshotHash=ga::bridge::sha256File(snapshotPath);
    next->inventoryHash=ga::bridge::sha256File(next->inventory);
    if(next->inventoryHash.empty()) throw std::runtime_error("live_inventory_hash_unavailable");
    if(next->snapshotHash.empty()) throw std::runtime_error("live_capture_hash_unavailable");
    session=std::move(next);
    return session->metadata();
}
std::string inspectSession(const std::string& token) { return checked(token).metadata(); }
void querySession(const std::string& token, const std::string& requestPath) {
    checked(token);
    ga::nativeQuery::queryObjectsFile(requestPath,[&]{ checked(token); });
}
void prepareSessionFaces(const std::string& token,const std::string& requestPath,
                         const std::string& outputPath) {
    auto& current=checked(token);
    if(ga::bridge::fileSize(requestPath)>1024*1024||std::ifstream(outputPath).good())
        throw std::runtime_error("invalid native face request size/output");
    std::ifstream input(requestPath);std::set<std::string> layers;std::string name;
    while(std::getline(input,name)) {
        if(name.empty()||name.size()>4096||name.find('\r')!=std::string::npos||layers.size()>=4096)
            throw std::runtime_error("invalid native face layer");
        layers.insert(name);
    }
    const auto requestHash=ga::bridge::sha256File(requestPath);
    const auto slash=requestPath.find_last_of('/');
    const auto cancel=requestPath.substr(0,slash+1)+"cancel-"+requestPath.substr(slash+8,32);
    ga::faces::prepareFaceLayers(*current.host,layers,[&]{return acedUsrBrk()!=0||access(cancel.c_str(),F_OK)==0;});
    checked(token);
    if(requestHash!=ga::bridge::sha256File(requestPath)) throw std::runtime_error("native face request changed");
    std::ostringstream out;out<<std::setprecision(17)<<"{\"session_id\":"<<q(token)
        <<",\"request_sha256\":"<<q(requestHash)<<",\"layers\":[";
    bool first=true;for(const auto& layer:layers){out<<(first?"":",")<<q(layer);first=false;}
    out<<']';ga::faces::emitCatalog(out,layers);out<<'}';
    if(!ga::bridge::writeAtomicText(outputPath,out.str())) throw std::runtime_error("native face publication failed");
}
}
