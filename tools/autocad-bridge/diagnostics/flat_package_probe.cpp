// Native repath of explicitly staged copies for a user-requested desktop kit.
// Not a new product import or a capture of the currently open document.
#define acrxEntryPoint flatLegacyEntryPoint
#include "capture_session_probe.cpp"
#undef acrxEntryPoint
#include "DbField.h"

namespace {
struct FlatFields {
    AcDbField::EvalOption previous=acdbGlobalFieldEvaluationOption();
    FlatFields() { ok(acdbSetGlobalFieldEvaluationOption(AcDbField::kDisable),"field scope"); }
    ~FlatFields() { acdbSetGlobalFieldEvaluationOption(previous); }
};
struct FlatContext {
    AcDbDatabase* previous=acdbHostApplicationServices()->workingDatabase();
    ~FlatContext() { acdbHostApplicationServices()->setWorkingDatabase(previous); }
};
std::string portableLeaf(std::string path) {
    for(char& c:path) if(c=='\\') c='/';
    return fs::path(path).filename().string();
}
void flatPrepare(const fs::path& root) {
    const auto input=root/"input",output=root/"package";
    if(fs::exists(output)) throw std::runtime_error("package already exists");
    fs::create_directory(output);
    std::set<std::string> names;
    for(const auto& file:fs::directory_iterator(input))
        if(file.is_regular_file() && file.path().extension()==".dwg") names.insert(file.path().filename().string());
    if(names.empty()) throw std::runtime_error("no DWG inputs");
    std::ostringstream changes; changes<<'['; bool first=true;
    FlatFields fields;
    for(const auto& name:names) {
        AcDbDatabase db(false,true);
        FlatContext context;
        ok(db.readDwgFile(AcString((input/name).string().c_str()).kwszPtr(),AcDbDatabase::kForReadAndAllShare,true),"read private input");
        ok(db.closeInput(true),"close private input");
        acdbHostApplicationServices()->setWorkingDatabase(&db);
        auto table=open<AcDbBlockTable>(db.blockTableId());
        AcDbBlockTableIterator* raw=nullptr; ok(table->newIterator(raw),"block iterator");
        std::unique_ptr<AcDbBlockTableIterator> it(raw);
        for(;!it->done();it->step()) {
            AcDbObjectId id; ok(it->getRecordId(id),"block id");
            auto record=open<AcDbBlockTableRecord>(id);
            if(!record->isFromExternalReference()) continue;
            AcString authored; ok(record->pathName(authored),"xref path");
            const auto old=utf(authored.kwszPtr()),leaf=portableLeaf(old);
            const bool available=names.count(leaf)!=0;
            const auto next=available?"./"+leaf:old;
            AcString recordName; ok(record->getName(recordName),"xref name");
            if(available) {
                ok(record->upgradeOpen(),"private record write");
                ok(record->setPathName(AcString(next.c_str()).kwszPtr()),"private relative path");
            }
            changes<<(first?"":",")<<"{\"drawing\":"<<q(name)<<",\"xref\":"<<q(utf(recordName.kwszPtr()))
                   <<",\"handle\":"<<q(handle(id))<<",\"before\":"<<q(old)<<",\"after\":"<<q(next)
                   <<",\"file_in_package\":"<<(available?"true":"false")<<'}'; first=false;
        }
        it.reset(); table.reset();
        ok(save(&db,output/name),"save private flat package");
    }
    changes<<']'; write(root/"repath.json",changes.str());
}
void flatVerify(const fs::path& root,const std::string& mainName) {
    if(fs::path(mainName).filename()!=mainName) throw std::runtime_error("entry must be a filename");
    FlatFields fields;
    {
        AcDbDatabase db(false,true);
        FlatContext context;
        ok(db.readDwgFile(AcString((root/"package"/mainName).string().c_str()).kwszPtr(),AcDbDatabase::kForReadAndAllShare,true),"read flat main drawing");
        ok(db.closeInput(true),"close flat main input");
        acdbHostApplicationServices()->setWorkingDatabase(&db);
        ok(acdbResolveCurrentXRefs(&db,false,false),"resolve flat package");
        std::ostringstream result; graph(result,&db); write(root/"verified-xrefs.json",result.str());
        // Native snapshot verifies model-space class/layer/transform/curve
        // observations, not full arbitrary-object geometric equivalence.
        write(root/"verified-model.json",snapshot(&db));
    }
    write(root/"verification-cleanup.json","{\"database_destroyed\":true}");
}
void flatCommand() {
    ACHAR argument[4096]={}; if(acedGetString(1,L"\nFlat package request: ",argument)!=RTNORM) return;
    fs::path root;
    try {
        std::ifstream input(utf(argument)); std::string mode,dir,entry;
        std::getline(input,mode); std::getline(input,dir); std::getline(input,entry);
        root=fs::canonical(dir);
        if(root.string().find("/artifacts/native-session-capture-20260923/desktop-kit-")==std::string::npos)
            throw std::runtime_error("owned desktop-kit stage required");
        if(mode=="flat-prepare") flatPrepare(root);
        else if(mode=="flat-verify") flatVerify(root,entry);
        else throw std::runtime_error("unknown flat package mode");
        acutPrintf(L"\nFLAT_PACKAGE_COMPLETE");
    } catch(const std::exception& error) {
        if(!root.empty()) { std::ofstream out(root/"native-error.txt",std::ios::app); out<<error.what()<<'\n'; }
        acutPrintf(L"\nFLAT_PACKAGE_ERROR: %s",AcString(error.what()).kwszPtr());
    }
}
}
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode message,void* id) {
    if(message==AcRx::kInitAppMsg) {
        acrxDynamicLinker->unlockApplication(id); acrxDynamicLinker->registerAppMDIAware(id);
        acedRegCmds->addCommand(L"GA_FLAT_PACKAGE",L"GACAPTURESESSIONPROBE",L"GACAPTURESESSIONPROBE",ACRX_CMD_MODAL,flatCommand);
    } else if(message==AcRx::kUnloadAppMsg) acedRegCmds->removeGroup(L"GA_FLAT_PACKAGE");
    return AcRx::kRetOK;
}
