// Diagnostic only: test public Mac AcDb termination before global destructors.
// Never install in GUI AutoCAD. No geometry, protocol, exit-code override,
// direct cache access, or forced process termination.
#include "rxregsvc.h"
#include "acdocman.h"
#include "dbmain.h"
#include <cstdio>
#include <cstdlib>

namespace {
void terminateAtProcessExit() {
    // This test must not shut down a live document or run from arxunload.
    const bool hasDocument = acDocManager && acDocManager->curDocument();
    std::fprintf(stderr, "\nGA_EXIT_LIFECYCLE_ATEXIT active_document=%d\n", hasDocument);
    std::fflush(stderr);
    if (hasDocument) return;
    // ObjectARX 2027 inc/dbmain.h: explicit Mac init/term; repeated calls ignored.
    acdbTerminate();
    std::fprintf(stderr, "GA_EXIT_LIFECYCLE_ACDB_TERMINATED\n");
    std::fflush(stderr);
}
}

extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode message, void* appId) {
    if (message == AcRx::kInitAppMsg) {
        acrxDynamicLinker->registerAppMDIAware(appId);
        // Deliberately keep this diagnostic module locked until process exit.
        if (std::atexit(terminateAtProcessExit) != 0) return AcRx::kRetError;
        std::fprintf(stderr, "\nGA_EXIT_LIFECYCLE_ARMED\n");
        std::fflush(stderr);
    }
    return AcRx::kRetOK;
}
