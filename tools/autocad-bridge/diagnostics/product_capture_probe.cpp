// Scratch harness calls the production capture implementation, not a copied
// recipe. Never loaded into the user's editor. Existing seed supplies nested,
// transformed, unloaded and unsaved XREF controls.
#define acrxEntryPoint captureFixtureEntryPoint
#include "capture_session_probe.cpp"
#undef acrxEntryPoint
#include "../native/file_io.cpp"
#include "../native/session_capture_instances.cpp"
#include "../native/session_capture.cpp"

namespace {
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
        if (mode == "seed") seed(root);
        else if (mode == "capture") {
            auto* host = acdbHostApplicationServices()->workingDatabase();
            if (fs::path(filename(host)).parent_path() != root / "source")
                throw std::runtime_error("not own scratch source");
            if (edit != "clean") { unsaved(host); unsavedXref(host); }
            write(root / "before.json", snapshot(host));
            ga::capture::capturePackage(*host, (root / "package").string());
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
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode message, void* id) {
    if (message == AcRx::kInitAppMsg) {
        acrxDynamicLinker->unlockApplication(id);
        acrxDynamicLinker->registerAppMDIAware(id);
        acedRegCmds->addCommand(L"GA_PRODUCT_CAPTURE_TEST", L"GACAPTURESESSIONPROBE",
                              L"GACAPTURESESSIONPROBE", ACRX_CMD_MODAL, productCaptureCommand);
    } else if (message == AcRx::kUnloadAppMsg) acedRegCmds->removeGroup(L"GA_PRODUCT_CAPTURE_TEST");
    return AcRx::kRetOK;
}
