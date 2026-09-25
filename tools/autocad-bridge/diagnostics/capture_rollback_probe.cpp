// Scratch-only causal control, not installed or called by GAOPEN.
// Check whether public transaction abort restores WBLOCK/XREF side effects.
#define GA_CAPTURE_CLONE_NO_ENTRY
#include "capture_session_clone_probe.cpp"
#include "dbtrans.h"
#include "DbField.h"

namespace {
struct SourceEvents:AcDbDatabaseReactor {
    std::vector<AcDbDatabase*> databases;
    std::map<std::string,unsigned> events;
    explicit SourceEvents(AcDbDatabase* host) {
        databases.push_back(host);
        AcDbXrefGraph graph;
        ok(acdbGetHostDwgXrefGraph(host,graph,true),"event graph");
        for(int i=1;i<graph.numNodes();++i) {
            auto* db=graph.xrefNode(i)->database();
            if(db && std::find(databases.begin(),databases.end(),db)==databases.end()) databases.push_back(db);
        }
        for(auto* db:databases) db->addReactor(this);
    }
    ~SourceEvents() override { for(auto* db:databases) db->removeReactor(this); }
    void headerSysVarChanged(const AcDbDatabase* db,const ACHAR* name,bool success) override {
        ++events["header:"+dbkey(const_cast<AcDbDatabase*>(db))+":"+utf(name)+":"+(success?"ok":"failed")];
    }
    void objectModified(const AcDbDatabase* db,const AcDbObject* object) override {
        ++events["object:"+dbkey(const_cast<AcDbDatabase*>(db))+":"+handle(object->objectId())+":"+utf(object->isA()->name())];
    }
    void output(const fs::path& path) {
        std::ostringstream out; out<<'{'; bool first=true;
        for(const auto& [key,count]:events) { out<<(first?"":",")<<q(key)<<':'<<count; first=false; }
        out<<'}'; write(path,out.str());
    }
};
struct AbortScope {
    AcDbTransactionManager* manager;
    int initial;
    bool active = false;
    explicit AbortScope(AcDbDatabase* db):manager(db->transactionManager()),
        initial(manager ? manager->numActiveTransactions() : -1) {
        if(!manager || !manager->startTransaction())
            throw std::runtime_error("transaction start failed");
        active = true;
    }
    void abort() {
        ok(manager->abortTransaction(), "abort source transaction");
        active = false;
        if(manager->numActiveTransactions() != initial)
            throw std::runtime_error("transaction depth changed");
    }
    ~AbortScope() { if(active) manager->abortTransaction(); }
};

struct CopyFieldScope {
    AcDbField::EvalOption previous=acdbGlobalFieldEvaluationOption();
    bool disabled;
    explicit CopyFieldScope(bool disable):disabled(disable) {
        if(disabled) ok(acdbSetGlobalFieldEvaluationOption(AcDbField::kDisable),"copy field scope");
    }
    ~CopyFieldScope() { if(disabled) acdbSetGlobalFieldEvaluationOption(previous); }
};
void rollbackControl(const fs::path& root, std::string variant, bool edit) {
    auto* host = acdbHostApplicationServices()->workingDatabase();
    if(fs::path(filename(host)).parent_path() != root/"source")
        throw std::runtime_error("own scratch host required");
    const bool disableFields=variant.rfind("field-",0)==0;
    if(disableFields) variant=variant.substr(6);
    if(variant != "passive" && variant != "symbols" && variant != "wblock"
        && variant != "restored-wblock" && variant != "object-edit"
        && variant != "model-wblock" && variant != "xref-block")
        throw std::runtime_error("unknown rollback control");
    if(edit) { unsaved(host); scratchXrefEditWithoutRestore(host); }
    write(root/"before-control.json", snapshot(host));
    SourceEvents events(host);
    {
        CopyFieldScope fields(disableFields);
        AbortScope rollback(host);
        // Positive control: a source write obtained via transaction, not our
        // open/close helper, must actually disappear on abort.
        if(variant == "object-edit") {
            auto ids = contents(acdbSymUtil()->blockModelSpaceId(host));
            bool changed = false;
            for(auto id:ids) {
                AcDbEntity* entity = nullptr;
                ok(rollback.manager->getObject(entity,id,AcDb::kForWrite),"control entity");
                if(auto* line = AcDbLine::cast(entity)) {
                    ok(line->setEndPoint({998.5,997.25,0}),"control endpoint");
                    changed = true; break;
                }
                if(auto* polyline = AcDbPolyline::cast(entity)) {
                    ok(polyline->setPointAt(0,{998.5,997.25}),"control vertex");
                    changed = true; break;
                }
            }
            if(!changed) throw std::runtime_error("no curve for transaction positive control");
        } else if(variant == "wblock" || variant == "model-wblock" || variant == "xref-block") {
            AcDbDatabase* raw = nullptr;
            MapReactor reactor(host);
            Acad::ErrorStatus status;
            if(variant == "wblock") status=host->wblock(raw);
            else if(variant == "model-wblock") status=host->wblock(raw,acdbSymUtil()->blockModelSpaceId(host));
            else {
                AcDbXrefGraph graph;
                ok(acdbGetHostDwgXrefGraph(host,graph,true),"block control graph");
                AcDbObjectId block;
                for(int i=1;i<graph.numNodes();++i) {
                    auto* node=graph.xrefNode(i);
                    if(node->xrefStatus()==AcDb::kXrfResolved && node->database()) { block=node->btrId(); break; }
                }
                if(block.isNull()) throw std::runtime_error("no loaded XREF block");
                status=host->wblock(raw,block);
            }
            std::unique_ptr<AcDbDatabase> copy(raw);
            write(root/"copy-result.json","{\"status\":"+std::to_string(int(status))+",\"maps\":["+reactor.events.str()+"]}");
            if(status!=Acad::eOk) {
                events.output(root/"source-events.json");
                ok(status,"control wblock");
            }
            if(!copy || copy.get() == host) throw std::runtime_error("source aliases copy");
            ok(save(copy.get(),root/"private-copy.dwg"),"save independent copy");
        } else if(variant == "restored-wblock") {
            cloneCapture(root,"restored-wblock",false);
        } else if(variant == "symbols") {
            AcDbXrefGraph graph;
            ok(acdbGetHostDwgXrefGraph(host,graph,true),"control graph");
            for(int i=1;i<graph.numNodes();++i) {
                auto* node = graph.xrefNode(i);
                if(node->xrefStatus()!=AcDb::kXrfResolved || !node->database()) continue;
                SymbolRestore symbols(node->database());
                ok(symbols.begin(),"control restore");
                ok(symbols.end(),"control forward");
            }
        }
        // Transaction-owned object pointers must not be reopened/closed by
        // snapshot until after abort. DBMOD is still observable beforehand.
        std::ostringstream varsDuring; vars(varsDuring);
        write(root/"during-vars.json",varsDuring.str());
        rollback.abort();
    }
    events.output(root/"source-events.json");
    write(root/"after-abort.json",snapshot(host));
}

void rollbackCommand() {
    ACHAR argument[4096] = {};
    if(acedGetString(1,L"\nRollback scratch request: ",argument)!=RTNORM) return;
    fs::path root;
    try {
        std::ifstream input(utf(argument)); std::string mode,dir,variant,edit;
        std::getline(input,mode); std::getline(input,dir);
        std::getline(input,variant); std::getline(input,edit);
        root=fs::canonical(dir);
        if(root.string().find("/artifacts/native-session-capture-20260923/")==std::string::npos)
            throw std::runtime_error("scratch root required");
        if(mode=="seed") seed(root);
        else if(mode=="rollback") rollbackControl(root,variant,edit!="clean");
        else throw std::runtime_error("unknown mode");
        acutPrintf(L"\nCAPTURE_ROLLBACK_COMPLETE");
    } catch(const std::exception& error) {
        if(!root.empty()) { std::ofstream log(root/"native-error.txt",std::ios::app); log<<error.what()<<'\n'; }
        acutPrintf(L"\nCAPTURE_ROLLBACK_ERROR: %s",AcString(error.what()).kwszPtr());
    }
}
}
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode message,void* id) {
    if(message==AcRx::kInitAppMsg) {
        acrxDynamicLinker->unlockApplication(id); acrxDynamicLinker->registerAppMDIAware(id);
        acedRegCmds->addCommand(L"GA_CAPTURE_ROLLBACK",L"GACAPTURESESSIONPROBE",L"GACAPTURESESSIONPROBE",ACRX_CMD_MODAL,rollbackCommand);
    } else if(message==AcRx::kUnloadAppMsg) acedRegCmds->removeGroup(L"GA_CAPTURE_ROLLBACK");
    return AcRx::kRetOK;
}
