// Scratch harness calls the production capture implementation, not a copied
// recipe. Never loaded into the user's editor. Existing seed supplies nested,
// transformed, unloaded and unsaved XREF controls.
#define acrxEntryPoint captureFixtureEntryPoint
#include "capture_session_probe.cpp"
#undef acrxEntryPoint
#include "../native/file_io.cpp"
#include "../native/session_capture_instances.cpp"
#include "../native/session_capture.cpp"
#include "../native/capture_commands.h"
#include <dlfcn.h>

namespace {
std::string captureProductionInputs(AcDbDatabase& host,const fs::path& root) {
    const char* libraryPath=std::getenv("GA_PRODUCTION_CAPTURE_LIBRARY");
    if(!libraryPath||!*libraryPath) return {};
    // dlopen exposes the built production functions without invoking the GUI
    // plugin entry point, menus, idle callbacks or global editor MCP queue.
    void* library=dlopen(libraryPath,RTLD_NOW|RTLD_LOCAL);
    if(!library) throw std::runtime_error(std::string("production capture library: ")+dlerror());
    using Capture=bool(*)(const std::string&,std::string&,const ga::bridge::CaptureObserver&);
    using Inventory=void(*)(AcDbDatabase&,const ga::bridge::DrawingGeometry&,const std::string&);
    auto capture=reinterpret_cast<Capture>(dlsym(library,"_ZN2ga6bridge29captureLiveSnapshotForServiceERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEERS7_RKNS1_8functionIFvRKNS0_15DrawingGeometryEEEE"));
    auto inventory=reinterpret_cast<Inventory>(dlsym(library,"_ZN2ga9liveQuery14writeInventoryER12AcDbDatabaseRKNS_6bridge15DrawingGeometryERKNSt3__112basic_stringIcNS7_11char_traitsIcEENS7_9allocatorIcEEEE"));
    if(!capture||!inventory) throw std::runtime_error("production capture symbols unavailable");
    const auto path=(root/"Drawing.autocad.json").string();
    std::string error;
    if(!capture(path,error,[&](const ga::bridge::DrawingGeometry& drawing) {
        inventory(host,drawing,path+".inventory.json");
    })) throw std::runtime_error(error);
    return ga::bridge::sha256File(path);
}
void productCaptureCommand() {
    ACHAR argument[4096] = {};
    if (acedGetString(1, L"\nCapture scratch request: ", argument) != RTNORM) return;
    fs::path root;
    try {
        std::ifstream input(utf(argument));
        std::string mode, directory, variant, edit;
        std::getline(input, mode); std::getline(input, directory);
        std::getline(input, variant); std::getline(input, edit);
        root = fs::canonical(directory);
        if (root.string().find("/artifacts/native-session-capture-20260923/product-") == std::string::npos)
            throw std::runtime_error("own product capture scratch root required");
        if (mode == "seed") seed(root,variant=="complete");
        else if (mode == "capture") {
            auto* host = acdbHostApplicationServices()->workingDatabase();
            if (fs::path(filename(host)).parent_path() != root / "source")
                throw std::runtime_error("not own scratch source");
            if (edit != "clean") { unsaved(host); unsavedXref(host); }
            write(root / "before.json", snapshot(host));
            const auto displaySha=captureProductionInputs(*host,root);
            ga::capture::capturePackage(*host, (root / "package").string(),displaySha);
            write(root / "after.json", snapshot(host));
        } else if (mode == "inspect") {
            write(root / "reopened.json", snapshot(acdbHostApplicationServices()->workingDatabase()));
        } else throw std::runtime_error("unknown product capture mode");
        acutPrintf(L"\nPRODUCT_CAPTURE_COMPLETE");
    } catch (const std::exception& error) {
        if (!root.empty()) { std::ofstream out(root / "native-error.txt", std::ios::app); out << error.what() << '\n'; }
        acutPrintf(L"\nPRODUCT_CAPTURE_ERROR: %s", AcString(error.what()).kwszPtr());
    }
}
}
#ifndef GA_PRODUCT_CAPTURE_ENTRY
#define GA_PRODUCT_CAPTURE_ENTRY acrxEntryPoint
#endif
extern "C" AcRx::AppRetCode GA_PRODUCT_CAPTURE_ENTRY(AcRx::AppMsgCode message, void* id) {
    if (message == AcRx::kInitAppMsg) {
        acrxLoadModule(L"AcGeomentObj.dbx",0);
        acrxDynamicLinker->loadModule(L"AcBr.dbx",1);
        acrxDynamicLinker->unlockApplication(id);
        acrxDynamicLinker->registerAppMDIAware(id);
        acedRegCmds->addCommand(L"GA_PRODUCT_CAPTURE_TEST", L"GACAPTURESESSIONPROBE",
                              L"GACAPTURESESSIONPROBE", ACRX_CMD_MODAL, productCaptureCommand);
    } else if (message == AcRx::kUnloadAppMsg) acedRegCmds->removeGroup(L"GA_PRODUCT_CAPTURE_TEST");
    return AcRx::kRetOK;
}
