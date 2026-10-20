#include "filesystem_utf8.h"
#include "live_query_session.h"
#include "live_inventory.h"
#include "session_capture.h"
#include "native_face_catalog.h"
#include "native_query_command.h"
#include "capture_commands.h"
#include "file_io.h"
#include "bridge_config.h"
#include "document_lock_policy.h"
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
#include <cstdlib>
#include <map>
#include <vector>
#if defined(__APPLE__)
#include <execinfo.h>
#endif
#ifdef _WIN32
#include "local_platform.h"
#else
#include <unistd.h>
#endif

namespace ga::liveQuery {
namespace {
std::string q(const std::string& s) { return "\""+ga::bridge::jsonEscape(s)+"\""; }
constexpr std::size_t kMaximumCaptureEvents = 32;
constexpr std::size_t kMaximumCaptureStackFrames = 24;
constexpr std::size_t kMaximumCaptureDiagnosticBytes = 64 * 1024;
class Session final : public AcDbDatabaseReactor {
public:
    AcApDocument* document = nullptr;
    AcDbDatabase* host = nullptr;
    std::set<AcDbDatabase*> watched;
    std::string token, source, sourceHash, snapshot, snapshotHash, inventory, inventoryHash;
    bool changed = false;
    unsigned missingXrefs = 0;
    int captureLockMode = 0, archiveLockMode = 0;
    std::string archiveLockStrategy = "not_requested";
    std::string phase = "watching";
    std::map<std::string, unsigned> eventCounts;
    std::vector<std::string> firstEvents, firstEventStack;
    ~Session() override { for(auto* db:watched) db->removeReactor(this); }
    void recordChange(const char* event, const AcDbDatabase* db,
                      const AcDbObject* object = nullptr, const ACHAR* variable = nullptr) noexcept {
        // Invalidation happens before diagnostics. Logging must never suppress
        // a real notification or throw back into AutoCAD's reactor dispatch.
        changed = true;
        try {
            const auto kind = phase + ":" + event;
            if (eventCounts.size() < kMaximumCaptureEvents || eventCounts.count(kind))
                ++eventCounts[kind];
            if (firstEvents.size() < kMaximumCaptureEvents) {
                std::string handle, type;
                if (object) {
                    AcDbHandle nativeHandle;
                    ACHAR text[32] = {};
                    object->getAcDbHandle(nativeHandle);
                    nativeHandle.getIntoAsciiBuffer(text);
                    handle = AcString(text).utf8Str();
                    type = std::string(AcString(object->isA()->name()).utf8Str()).substr(0, 512);
                }
                firstEvents.push_back("{\"phase\":" + q(phase) +
                    ",\"event\":" + q(event) + ",\"database\":" +
                    q(db == host ? "host" : "xref") + ",\"handle\":" + q(handle) +
                    ",\"class\":" + q(type) + ",\"variable\":" +
                    q(variable ? AcString(variable).utf8Str() : "") + "}");
            }
            // Private causal qualification can opt into one bounded native
            // stack; normal captures contain only event identities/counts.
#if defined(__APPLE__)
            const char* enabled = std::getenv("GA_LIVE_CAPTURE_DIAGNOSTICS");
            if (firstEventStack.empty() && enabled && std::string(enabled) == "1") {
                void* frames[kMaximumCaptureStackFrames];
                const int count = backtrace(frames, kMaximumCaptureStackFrames);
                char** symbols = backtrace_symbols(frames, count);
                if (symbols) {
                    for (int i = 0; i < count; ++i)
                        firstEventStack.push_back(std::string(symbols[i]).substr(0, 1024));
                    std::free(symbols);
                }
            }
#endif
        } catch (...) {}
    }
    void objectAppended(const AcDbDatabase* db, const AcDbObject* object) override {
        recordChange("object_appended", db, object);
    }
    void objectUnAppended(const AcDbDatabase* db, const AcDbObject* object) override {
        recordChange("object_unappended", db, object);
    }
    void objectReAppended(const AcDbDatabase* db, const AcDbObject* object) override {
        recordChange("object_reappended", db, object);
    }
    void objectModified(const AcDbDatabase* db, const AcDbObject* object) override {
        recordChange("object_modified", db, object);
    }
    void objectErased(const AcDbDatabase* db, const AcDbObject* object, bool) override {
        recordChange("object_erased", db, object);
    }
    void headerSysVarChanged(const AcDbDatabase* db, const ACHAR* name, bool success) override {
        // Pan/zoom is not an edit of the calculation basis; unit changes are.
        if(success && AcString(name)==_T("INSUNITS"))
            recordChange("units_changed", db, nullptr, name);
    }
    void goodbye(const AcDbDatabase* db) override {
        recordChange("database_closed", db); watched.erase(const_cast<AcDbDatabase*>(db));
    }
    void watch(AcDbDatabase* db) {
        if(db && watched.insert(db).second) db->addReactor(this);
    }
    void publishDiagnostics(const char* outcome, const std::string& error = {}) const noexcept {
        try {
            auto* current = acDocManager ? acDocManager->curDocument() : nullptr;
            auto* services = acdbHostApplicationServices();
            resbuf modification = {};
            const bool modificationKnown = acedGetVar(_T("DBMOD"), &modification) == RTNORM &&
                modification.restype == RTSHORT;
            std::ostringstream out;
            out << "{\"schema\":\"green-atlas.live-capture-diagnostics/1\",\"outcome\":"
                << q(outcome) << ",\"error\":" << q(error) << ",\"phase\":" << q(phase)
                << ",\"changed\":" << (changed ? "true" : "false")
                << ",\"document_present\":" << (current ? "true" : "false")
                << ",\"document_matches\":" << (current == document ? "true" : "false")
                << ",\"document_database_matches\":"
                << (current && current->database() == host ? "true" : "false")
                << ",\"working_database_matches\":"
                << (services && services->workingDatabase() == host ? "true" : "false")
                << ",\"capture_my_lock_mode\":" << captureLockMode
                << ",\"archive_my_lock_mode\":" << archiveLockMode
                << ",\"archive_lock_strategy\":" << q(archiveLockStrategy)
                << ",\"current_execution_lock_mode\":"
                << (current ? std::to_string(int(current->myLockMode())) : "null")
                << ",\"dbmod\":" << (modificationKnown ? std::to_string(modification.resval.rint) : "null")
                << ",\"watched_databases\":" << watched.size() << ",\"event_counts\":{";
            bool first = true;
            for (const auto& [event, count] : eventCounts) {
                out << (first ? "" : ",") << q(event) << ':' << count;
                first = false;
            }
            out << "},\"first_events\":[";
            for (std::size_t i = 0; i < firstEvents.size(); ++i)
                out << (i ? "," : "") << firstEvents[i];
            out << "],\"first_event_stack\":[";
            for (std::size_t i = 0; i < firstEventStack.size(); ++i)
                out << (i ? "," : "") << q(firstEventStack[i]);
            out << "]}";
            const auto payload = out.str();
            if (payload.size() <= kMaximumCaptureDiagnosticBytes)
                ga::bridge::writeAtomicText(snapshot + ".session-diagnostics.json", payload);
        } catch (...) {}
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
        out<<"{\"session_id\":"<<q(token)<<",\"pid\":"<<
#ifdef _WIN32
            ga::platform::processId()
#else
            getpid()
#endif

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
Session& checked(const std::string& token, const char* phase = "session_validation") {
    if(!session || session->token!=token) throw std::runtime_error("live_session_expired");
    session->phase = phase;
    try { session->check(); }
    catch (const std::exception& failure) {
        // Record the failed predicate while still in the failing context.
        // A later document unlock could otherwise conceal workingDB changes.
        session->publishDiagnostics("failed", failure.what());
        throw;
    }
    return *session;
}
}
void closeSession() { ga::faces::clearCatalog(); session.reset(); }
std::string openSession(const std::string& token, const std::string& snapshotPath) {
    return openSession(token, snapshotPath, nullptr);
}

std::string openSession(const std::string& token, const std::string& snapshotPath,
                        std::string* transferFacts) {
    if(token.size()!=32 || token.find_first_not_of("0123456789abcdef")!=std::string::npos)
        throw std::runtime_error("live_session_invalid_token");
    auto* document=acDocManager?acDocManager->curDocument():nullptr;
    if(!document || document->database()!=acdbHostApplicationServices()->workingDatabase())
        throw std::runtime_error("live_document_unavailable");
    auto next=std::make_unique<Session>();
    next->document=document; next->host=document->database(); next->token=token;
    next->captureLockMode = int(document->myLockMode());
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
    std::string error, facts;
    try {
        next->phase = "capture_geometry";
        if(!ga::bridge::captureLiveSnapshotForService(snapshotPath,error,[&](const auto& drawing) {
            next->phase = "capture_inventory";
            writeInventory(*next->host,drawing,next->inventory);
            next->phase = "capture_serialization";
        }, transferFacts ? &facts : nullptr))
            throw std::runtime_error("live_capture_failed: "+error);
        next->phase = "capture_complete";
        next->check();
    } catch (const std::exception& failure) {
        next->publishDiagnostics("failed", failure.what());
        throw;
    }
    next->publishDiagnostics("checked");
    next->phase = "session";
    next->snapshotHash=ga::bridge::sha256File(snapshotPath);
    next->inventoryHash=ga::bridge::sha256File(next->inventory);
    if(next->inventoryHash.empty()) throw std::runtime_error("live_inventory_hash_unavailable");
    if(next->snapshotHash.empty()) throw std::runtime_error("live_capture_hash_unavailable");
    if (transferFacts) *transferFacts = "{\"snapshot_sha256\":"
        + q(next->snapshotHash) + ",\"probe\":" + facts + "}";
    session=std::move(next);
    return session->metadata();
}
std::string inspectSession(const std::string& token) { return checked(token).metadata(); }
std::string archiveSession(const std::string& token, const std::string& destination) {
    auto& current = checked(token, "archive_admission");
    const auto identity = current.metadata();
    // Registered modal commands already own the document's write lock. A
    // repeated lock can evaluate associative networks after watched capture.
    // Application-context archival still acquires its own lock and checks
    // any resulting invalidation; it never adopts another context's lock.
    struct DocumentLock {
        AcApDocument* document;
        bool acquired = false;
        explicit DocumentLock(Session& current) : document(current.document) {
            if (document) {
                const auto mode = document->myLockMode();
                current.archiveLockMode = int(mode);
                if (canBorrowDocumentWrite(mode)) {
                    current.archiveLockStrategy = "borrowed";
                    return;
                }
            }
            current.archiveLockStrategy = "owned";
            if (!document || !acDocManager ||
                acDocManager->lockDocument(document, AcAp::kWrite) != Acad::eOk) {
                current.publishDiagnostics("failed", "capture document lock unavailable");
                throw std::runtime_error("capture document lock unavailable");
            }
            acquired = true;
        }
        ~DocumentLock() { if (acquired) acDocManager->unlockDocument(document); }
    };
    current.phase = "archive_document_lock";
    DocumentLock lock(current);
    checked(token, "archive_locked_admission");
    current.phase = "archive_package";
    try { ga::capture::capturePackage(*current.host, destination, current.snapshotHash); }
    catch (const std::exception& failure) {
        current.publishDiagnostics("failed", failure.what());
        throw;
    }
    current.phase = "archive_complete";
    current.publishDiagnostics("archived");
    // The archive was made in this modal command after checking the session.
    // restore/forward XREF symbol notifications may invalidate later editor
    // queries. Retain those notifications; cached local geometry remains valid.
    return identity;
}
void querySession(const std::string& token, const std::string& requestPath) {
    checked(token);
    ga::nativeQuery::queryObjectsFile(requestPath,[&]{ checked(token); });
}
void prepareSessionFaces(const std::string& token,const std::string& requestPath,
                         const std::string& outputPath) {
    auto& current=checked(token);
    if(ga::bridge::fileSize(requestPath)>1024*1024||std::ifstream(ga::bridge::nativePath(outputPath)).good())
        throw std::runtime_error("invalid native face request size/output");
    std::ifstream input(ga::bridge::nativePath(requestPath));std::set<std::string> layers;std::string name;
    while(std::getline(input,name)) {
        if(name.empty()||name.size()>4096||name.find('\r')!=std::string::npos||layers.size()>=4096)
            throw std::runtime_error("invalid native face layer");
        layers.insert(name);
    }
    const auto requestHash=ga::bridge::sha256File(requestPath);
    const auto slash=requestPath.find_last_of('/');
    const auto cancel=requestPath.substr(0,slash+1)+"cancel-"+requestPath.substr(slash+8,32);
    ga::faces::prepareFaceLayers(*current.host,layers,[&]{return acedUsrBrk()!=0||
#ifdef _WIN32
        ga::platform::fileExists(cancel);
#else
        access(cancel.c_str(),F_OK)==0;
#endif
    });
    checked(token);
    if(requestHash!=ga::bridge::sha256File(requestPath)) throw std::runtime_error("native face request changed");
    std::ostringstream out;out<<std::setprecision(17)<<"{\"session_id\":"<<q(token)
        <<",\"request_sha256\":"<<q(requestHash)<<",\"layers\":[";
    bool first=true;for(const auto& layer:layers){out<<(first?"":",")<<q(layer);first=false;}
    out<<']';ga::faces::emitCatalog(out,layers);out<<'}';
    if(!ga::bridge::writeAtomicText(outputPath,out.str())) throw std::runtime_error("native face publication failed");
}
}
