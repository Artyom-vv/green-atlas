// Follow-up: native clone first; never call restore/save/repath on a live DB.
// Reuse the prior diagnostic fixture and observation helpers, not its capture.
#define acrxEntryPoint captureLegacyEntryPoint
#include "capture_session_probe.cpp"
#undef acrxEntryPoint
#include "dbidmap.h"

namespace {
std::string dbkey(AcDbDatabase* db) { return std::to_string(reinterpret_cast<uintptr_t>(db)); }
std::string idkey(AcDbObjectId id) { return std::to_string(id.asOldId()); }
std::string originalHandle(AcDbObjectId id) {
    ACHAR s[32]={}; id.nonForwardedHandle().getIntoAsciiBuffer(s); return utf(s);
}
void objectIdentity(std::ostream& out,AcDbObjectId id) {
    auto object=open<AcDbObject>(id);
    const auto owner=object->ownerId();
    out<<"{\"id\":"<<q(idkey(id))<<",\"handle\":"<<q(handle(id))
       <<",\"class\":"<<q(utf(object->isA()->name()))
       <<",\"database\":"<<q(dbkey(object->database()))
       <<",\"owner_id\":"<<q(idkey(owner))<<",\"owner_handle\":"<<q(handle(owner));
    if(!owner.isNull()) {
        auto owningObject=open<AcDbObject>(owner);
        out<<",\"owner_class\":"<<q(utf(owningObject->isA()->name()))
           <<",\"owner_database\":"<<q(dbkey(owningObject->database()));
    }
    if(auto* record=AcDbBlockTableRecord::cast(object.get())) {
        AcString name; ok(record->getName(name),"dependency name");
        out<<",\"name\":"<<q(utf(name.kwszPtr()));
    }
    out<<'}';
}
void nativeMap(std::ostream& o,AcDbIdMapping& mapping) {
    AcDbDatabase *source=nullptr,*target=nullptr; mapping.origDb(source); mapping.destDb(target);
    o<<"{\"source_database\":"<<q(dbkey(source))<<",\"target_database\":"<<q(dbkey(target))<<",\"pairs\":[";
    AcDbIdMappingIter it(mapping); bool first=true;
    for(it.start();!it.done();it.next()) {
        AcDbIdPair p; if(!it.getMap(p)) throw std::runtime_error("missing native pair");
        const auto a=p.key(), b=p.value();
        auto redirected=a; const bool forwarded=redirected.convertToRedirectedId();
        o<<(first?"":",")<<"{\"source_runtime_id\":"<<q(idkey(a))<<",\"source_handle\":"<<q(handle(a))
         <<",\"source_redirected_runtime_id\":"<<q(idkey(redirected))<<",\"source_id_was_forwarded\":"<<(forwarded?"true":"false")
         <<",\"source_original_handle\":"<<q(originalHandle(a))<<",\"source_database\":"<<q(dbkey(a.database()))
         <<",\"source_original_database\":"<<q(dbkey(a.originalDatabase()))<<",\"target_runtime_id\":"<<q(idkey(b))
         <<",\"target_handle\":"<<q(handle(b))<<",\"target_database\":"<<q(dbkey(b.database()))
         <<",\"is_cloned\":"<<(p.isCloned()?"true":"false")<<",\"is_primary\":"<<(p.isPrimary()?"true":"false")
         <<",\"owner_translated\":"<<(p.isOwnerXlated()?"true":"false")<<'}'; first=false;
    } o<<"]}";
}
struct MapReactor:AcRxEventReactor {
    AcDbDatabase* source; std::ostringstream events; unsigned count=0,notice=0;
    explicit MapReactor(AcDbDatabase* db):source(db) { acrxEvent->addReactor(this); }
    ~MapReactor() override { acrxEvent->removeReactor(this); }
    void wblockNotice(AcDbDatabase* db) override { if(db==source) { db->forceWblockDatabaseCopy(); ++notice; } }
    void endDeepClone(AcDbIdMapping& map) override {
        if(count++) events<<','; nativeMap(events,map);
    }
};
AcDbObjectIdArray contents(AcDbObjectId record) {
    auto r=open<AcDbBlockTableRecord>(record); AcDbBlockTableRecordIterator* raw=nullptr;
    ok(r->newIterator(raw),"contents iterator"); std::unique_ptr<AcDbBlockTableRecordIterator> it(raw);
    AcDbObjectIdArray ids; for(;!it->done();it->step()) { AcDbObjectId id; ok(it->getEntityId(id),"contents id"); ids.append(id); }
    return ids;
}
// Setup only: existing scratch XREF entity can be edited without restoring tables.
// The capture begins at before.json, after this explicit fixture edit.
void scratchXrefEditWithoutRestore(AcDbDatabase* host) {
    AcDbXrefGraph graph; ok(acdbGetHostDwgXrefGraph(host,graph,true),"setup graph");
    for(int i=1;i<graph.numNodes();++i) {
        auto* n=graph.xrefNode(i); if(utf(n->name())!="CHILD") continue;
        auto ids=contents(n->btrId());
        for(auto id:ids) {
            auto e=open<AcDbEntity>(id,AcDb::kForWrite);
            if(auto* line=AcDbLine::cast(e.get())) { ok(line->setEndPoint({13.75,4.125,0}),"setup XREF endpoint without restore"); return; }
        }
    }
    throw std::runtime_error("setup CHILD line not found");
}
struct CloneUnit {
    AcDbDatabase* source=nullptr;
    AcDbObjectId contentRecord;
    std::string key,file,authored,resolved;
    std::unique_ptr<AcDbDatabase> copy;
};
std::string shaText(const std::string& value) {
    unsigned char bytes[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(value.data(),static_cast<CC_LONG>(value.size()),bytes);
    std::ostringstream out;
    for(auto byte:bytes) out<<std::hex<<std::setw(2)<<std::setfill('0')<<int(byte);
    return out.str();
}
void observeSource(const fs::path& root,const std::string& variant) {
    auto* host=acdbHostApplicationServices()->workingDatabase();
    if(fs::path(filename(host)).parent_path()!=root/"source") throw std::runtime_error("not own scratch host");
    if(variant!="passive"&&variant!="restore-symbols") throw std::runtime_error("unknown observation mode");
    std::ostringstream states; states<<std::setprecision(17)<<'['; bool first=true;
    auto recordState=[&](const std::string& stage) {
        std::ostringstream entities,table;
        entities<<std::setprecision(17); bool firstEntity=true;
        walk(entities,acdbSymUtil()->blockModelSpaceId(host),AcGeMatrix3d(),"",firstEntity);
        records(table,host);
        states<<(first?"":",")<<"{\"stage\":"<<q(stage)<<",\"entities_sha256\":"<<q(shaText(entities.str()))
              <<",\"records_sha256\":"<<q(shaText(table.str()))<<",\"database\":";
        identity(states,host); states<<",\"vars\":"; vars(states);
        states<<",\"graph\":"; graph(states,host); states<<'}'; first=false;
    };
    recordState("before");
    AcDbXrefGraph graph; ok(acdbGetHostDwgXrefGraph(host,graph,true),"observation graph");
    for(int i=1;i<graph.numNodes();++i) {
        auto* node=graph.xrefNode(i);
        if(node->xrefStatus()!=AcDb::kXrfResolved||!node->database()) continue;
        if(variant=="restore-symbols") {
            SymbolRestore symbols(node->database()); ok(symbols.begin(),"observation restore");
            ok(symbols.end(),"observation forward");
        }
        recordState(handle(node->btrId()));
    }
    recordState("after"); states<<']'; write(root/"observations.json",states.str());
}
void cloneCapture(const fs::path& root,const std::string& variant,bool edit) {
    auto* host=acdbHostApplicationServices()->workingDatabase();
    if(fs::path(filename(host)).parent_path()!=root/"source") throw std::runtime_error("not own scratch host");
    write(root/"opened.json",snapshot(host));
    if(edit) { unsaved(host); scratchXrefEditWithoutRestore(host); }
    write(root/"before.json",snapshot(host));
    fs::create_directory(root/"raw");
    AcDbXrefGraph graph; ok(acdbGetHostDwgXrefGraph(host,graph,true),"capture graph");
    std::vector<CloneUnit> units;
    units.push_back({host,acdbSymUtil()->blockModelSpaceId(host),"0","host.dwg","","",nullptr});
    std::set<AcDbDatabase*> live{host};
    std::ostringstream pathMap,unloaded; unloaded<<'['; bool firstUnloaded=true;
    for(int i=1;i<graph.numNodes();++i) {
        auto* n=graph.xrefNode(i); auto record=open<AcDbBlockTableRecord>(n->btrId());
        AcString authored,resolved; record->pathName(authored);
        auto found=acdbHostApplicationServices()->findFile(resolved,authored.kwszPtr(),host,AcDbHostApplicationServices::kXRefDrawing);
        const auto key=handle(n->btrId());
        if(n->xrefStatus()==AcDb::kXrfResolved&&n->database()&&live.insert(n->database()).second) {
            const auto file="xref-"+key+".dwg";
            units.push_back({n->database(),n->btrId(),key,file,utf(authored.kwszPtr()),utf(resolved.kwszPtr()),nullptr});
            pathMap<<utf(authored.kwszPtr())<<'\n'<<utf(resolved.kwszPtr())<<'\n'<<file<<'\n';
        } else if(n->xrefStatus()==AcDb::kXrfUnloaded&&found==Acad::eOk) {
            const auto source=fs::path(utf(resolved.kwszPtr()));
            const auto relative=fs::relative(fs::canonical(source),fs::canonical(root/"source"));
            if(relative.empty()||*relative.begin()=="..") throw std::runtime_error("unloaded source outside scratch");
            const auto file="unloaded-"+key+".dwg", hash=sha(source.string());
            fs::copy_file(source,root/"raw"/file);
            if(hash.empty()||sha(source.string())!=hash||sha((root/"raw"/file).string())!=hash) throw std::runtime_error("unloaded SHA mismatch");
            pathMap<<utf(authored.kwszPtr())<<'\n'<<utf(resolved.kwszPtr())<<'\n'<<file<<'\n';
            unloaded<<(firstUnloaded?"":",")<<"{\"handle\":"<<q(key)<<",\"file\":"<<q(file)<<",\"source_sha256\":"<<q(hash)<<'}'; firstUnloaded=false;
        }
    }
    unloaded<<']'; write(root/"unloaded-copies.json",unloaded.str()); write(root/"archive-map.txt",pathMap.str());
    // Gather all mappings before any copy is destroyed; callback pointer identity
    // must never be inferred across independent allocations or processes.
    for(auto& unit:units) {
        write(root/("starting-clone-"+unit.key+".json"),"{\"variant\":"+q(variant)+",\"unit\":"+q(unit.key)+"}");
        AcDbDatabase* raw=nullptr; Acad::ErrorStatus status=Acad::eOk;
        std::ostringstream maps; unsigned notices=0;
        if(unit.key=="0"||variant=="whole-wblock"||variant=="selected-wblock"||variant=="restored-wblock") {
            // Diagnostic only: SDK-prescribed forwarding scope. Unlike the
            // clone-first candidate this temporarily changes loaded symbols;
            // the unchanged-source guards still apply, including DBMOD.
            SymbolRestore symbols(unit.source);
            if(unit.key!="0"&&variant=="restored-wblock") ok(symbols.begin(),"restore before native wblock");
            MapReactor reactor(unit.source);
            if(unit.key=="0"||variant=="whole-wblock"||variant=="restored-wblock") status=unit.source->wblock(raw);
            else { auto ids=contents(unit.contentRecord); status=unit.source->wblock(raw,ids,AcGePoint3d::kOrigin); }
            maps<<'['<<reactor.events.str()<<']'; notices=reactor.notice;
            unit.copy.reset(raw);
            ok(symbols.end(),"forward after native wblock");
        } else if(variant=="clone-objects") {
            unit.copy=std::make_unique<AcDbDatabase>(true,true); raw=unit.copy.get();
            auto ids=contents(unit.contentRecord); AcDbIdMapping map;
            write(root/("clone-input-"+unit.key+".json"),"{\"count\":"+std::to_string(ids.length())+",\"source_database\":"+q(dbkey(unit.source))+",\"target_database\":"+q(dbkey(raw))+"}");
            // Loaded XREF symbols can forward to the host. Clone those native
            // records explicitly as primary roots before translating entities.
            AcDbObjectIdArray dependencies;
            for(auto id:ids) {
                auto e=open<AcDbEntity>(id);
                if(auto* ref=AcDbBlockReference::cast(e.get())) {
                    auto target=ref->blockTableRecord(); target.convertToRedirectedId();
                    if(!dependencies.contains(target)) dependencies.append(target);
                }
            }
            if(!dependencies.isEmpty()) {
                std::ostringstream owners; owners<<"{\"target\":";
                objectIdentity(owners,raw->blockTableId()); owners<<",\"dependencies\":[";
                for(int i=0;i<dependencies.length();++i) {
                    owners<<(i?",":""); objectIdentity(owners,dependencies[i]);
                }
                owners<<"]}"; write(root/("dependency-owners-"+unit.key+".json"),owners.str());
                status=host->wblockCloneObjects(dependencies,raw->blockTableId(),map,AcDb::kDrcIgnore,true);
                write(root/("clone-dependencies-"+unit.key+".json"),"{\"status\":"+std::to_string(int(status))+",\"count\":"+std::to_string(dependencies.length())+"}");
                if(status!=Acad::eOk) {
                    std::ostringstream failedMap; nativeMap(failedMap,map);
                    write(root/("failed-map-"+unit.key+".json"),failedMap.str());
                    // Diagnostic isolation, not a fallback: each dependency is
                    // tried in a new private DB; the original failure remains.
                    for(int i=0;i<dependencies.length();++i) {
                        auto isolated=std::make_unique<AcDbDatabase>(true,true);
                        AcDbObjectIdArray one; one.append(dependencies[i]); AcDbIdMapping oneMap;
                        const auto oneStatus=host->wblockCloneObjects(one,isolated->blockTableId(),oneMap,AcDb::kDrcIgnore,false);
                        std::ostringstream trial; trial<<"{\"source_handle\":"<<q(handle(dependencies[i]))
                            <<",\"status\":"<<int(oneStatus)<<",\"map\":"; nativeMap(trial,oneMap); trial<<'}';
                        write(root/("dependency-trial-"+unit.key+"-"+std::to_string(i)+".json"),trial.str());
                    }
                    write(root/("after-failed-clone-"+unit.key+".json"),snapshot(host));
                }
                ok(status,"clone native XREF record dependencies");
            }
            status=unit.source->wblockCloneObjects(ids,acdbSymUtil()->blockModelSpaceId(raw),map,AcDb::kDrcIgnore,false);
            write(root/("clone-return-"+unit.key+".json"),"{\"status\":"+std::to_string(int(status))+"}");
            // The entity's unredirected XREF id is not necessarily translated
            // by WblockCloneObjects. Repair only the private reference using
            // its native entity map and the native redirected BTR map.
            std::ostringstream repairs; repairs<<'['; bool firstRepair=true;
            for(auto id:ids) {
                auto e=open<AcDbEntity>(id);
                auto* ref=AcDbBlockReference::cast(e.get()); if(!ref) continue;
                const auto original=ref->blockTableRecord(); auto redirected=original; redirected.convertToRedirectedId();
                AcDbIdPair entityPair,recordPair; entityPair.setKey(id); recordPair.setKey(redirected);
                if(!map.compute(entityPair)||!map.compute(recordPair)||entityPair.value().isNull()||recordPair.value().isNull())
                    throw std::runtime_error("native reference repair mapping absent");
                auto copyRef=open<AcDbBlockReference>(entityPair.value(),AcDb::kForWrite);
                const auto beforeTarget=copyRef->blockTableRecord();
                ok(copyRef->setBlockTableRecord(recordPair.value()),"private native reference repair");
                if(original!=redirected) ok(map.assign(AcDbIdPair(original,recordPair.value(),false,false,true)),"native forwarded alias map");
                repairs<<(firstRepair?"":",")<<"{\"source_entity\":"<<q(idkey(id))<<",\"source_reference_id\":"<<q(idkey(original))
                 <<",\"native_redirected_reference_id\":"<<q(idkey(redirected))<<",\"archive_entity\":"<<q(handle(entityPair.value()))
                 <<",\"archive_record\":"<<q(handle(recordPair.value()))<<",\"copy_reference_before\":"<<q(handle(beforeTarget))
                 <<",\"method\":\"IdMapping.compute + convertToRedirectedId + private setBlockTableRecord\"}"; firstRepair=false;
            }
            repairs<<']'; write(root/("native-reference-repairs-"+unit.key+".json"),repairs.str());
            maps<<'['; nativeMap(maps,map); maps<<']';
            if(status==Acad::eOk) raw->setInsunits(unit.source->insunits());
        } else throw std::runtime_error("unknown clone variant");
        std::ostringstream result; result<<"{\"unit\":"<<q(unit.key)<<",\"file\":"<<q(unit.file)
          <<",\"source_database\":"<<q(dbkey(unit.source))<<",\"target_database\":"<<q(dbkey(raw))
          <<",\"clone_status\":"<<int(status)<<",\"force_copy_notices\":"<<notices
          <<",\"target_is_live_database\":"<<(live.count(raw)?"true":"false")<<",\"events\":"<<maps.str()<<'}';
        write(root/("native-map-"+unit.key+".json"),result.str());
        write(root/("after-clone-"+unit.key+".json"),snapshot(host));
        if(status!=Acad::eOk||!raw||live.count(raw)) throw std::runtime_error("clone failed or aliases live database");
    }
    // Copies are standalone DBs. Try restoration only on the independent copy;
    // eNotApplicable means there is no active XREF forwarding to restore there.
    std::ostringstream actions; actions<<'['; bool first=true;
    for(auto& unit:units) {
        auto* copy=unit.copy.get();
        if(live.count(copy)) throw std::runtime_error("copy operation on live DB refused");
        Acad::ErrorStatus restore=unit.key=="0"?Acad::eNotApplicable:copy->restoreOriginalXrefSymbols();
        // Capture topology in the private DB without loading source XREFs again.
        std::ostringstream table; records(table,copy); write(root/("copy-records-"+unit.key+".json"),table.str());
        const auto saved=save(copy,root/"raw"/unit.file);
        actions<<(first?"":",")<<"{\"unit\":"<<q(unit.key)<<",\"file\":"<<q(unit.file)
               <<",\"copy_restore_status\":"<<int(restore)<<",\"save_status\":"<<int(saved)
               <<",\"live_restore_called\":false,\"live_save_called\":false}"; first=false;
        write(root/("after-save-copy-"+unit.key+".json"),snapshot(host));
        if(saved!=Acad::eOk) throw std::runtime_error("copy save failed");
    }
    actions<<']'; write(root/"clone-actions.json",actions.str());
    write(root/"before-copy-destruction.json",snapshot(host));
    units.clear(); write(root/"after.json",snapshot(host));
}
void cloneCommand() {
    ACHAR argument[4096]={}; if(acedGetString(1,L"\nClone scratch request: ",argument)!=RTNORM) return;
    fs::path root;
    try {
        std::ifstream input(utf(argument)); std::string mode,dir,variant,edit;
        std::getline(input,mode); std::getline(input,dir); std::getline(input,variant); std::getline(input,edit);
        root=fs::canonical(dir);
        if(root.string().find("/artifacts/native-session-capture-20260923/")==std::string::npos) throw std::runtime_error("scratch root required");
        if(mode=="seed") seed(root);
        else if(mode=="capture") cloneCapture(root,variant,edit!="clean");
        else if(mode=="observe") observeSource(root,variant);
        else if(mode=="repath") repath(root);
        else if(mode=="inspect") write(root/(variant+".json"),snapshot(acdbHostApplicationServices()->workingDatabase()));
        else throw std::runtime_error("unknown mode");
        acutPrintf(L"\nCAPTURE_CLONE_COMPLETE");
    } catch(const std::exception& error) {
        if(!root.empty()) { std::ofstream log(root/"native-error.txt",std::ios::app); log<<error.what()<<'\n'; }
        acutPrintf(L"\nCAPTURE_CLONE_ERROR: %s",AcString(error.what()).kwszPtr());
    }
}
}
#ifndef GA_CAPTURE_CLONE_NO_ENTRY
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode message,void* id) {
    if(message==AcRx::kInitAppMsg) {
        acrxDynamicLinker->unlockApplication(id); acrxDynamicLinker->registerAppMDIAware(id);
        acedRegCmds->addCommand(L"GA_CAPTURE_CLONE_PROBE",L"GACAPTURESESSIONPROBE",L"GACAPTURESESSIONPROBE",ACRX_CMD_MODAL,cloneCommand);
    } else if(message==AcRx::kUnloadAppMsg) acedRegCmds->removeGroup(L"GA_CAPTURE_CLONE_PROBE");
    return AcRx::kRetOK;
}
#endif
