#if defined(_DEBUG) && !defined(AC_FULL_DEBUG)
#error _DEBUG should not be defined for a release ObjectARX build
#endif

#include "rxregsvc.h"
#include "aced.h"
#include "core_rxmfcapi.h"
#include "capture_commands.h"
#include "mcp_transport.h"
#include "delivery_command.h"
#include "delivery_ui.h"
#include "native_query_command.h"
#include "live_query_session.h"
#include "live_query_transport.h"

namespace ga::bridge {

namespace {
constexpr const ACHAR* kCommandGroup = _T("GREEN_ATLAS");
}

void initialize() {
    acedRegCmds->addCommand(kCommandGroup, _T("GALIVESESSION"), _T("GALIVESESSION"),
                           ACRX_CMD_MODAL, ga::liveQuery::requestCommand);
    acedRegCmds->addCommand(kCommandGroup, _T("GADEMOLOOP"), _T("GADEMOLOOP"),
                           ACRX_CMD_MODAL, ga::liveQuery::consoleDemoLoop);
    acedRegCmds->addCommand(kCommandGroup, _T("GAQUERYOBJECTS"), _T("GAQUERYOBJECTS"),
                            ACRX_CMD_MODAL, ga::nativeQuery::queryObjectsCommand);
#ifdef GA_GEOMETRY_SELFTEST
    acedRegCmds->addCommand(kCommandGroup, _T("GAGEOMETRYSELFTEST"),
                            _T("GAGEOMETRYSELFTEST"), ACRX_CMD_MODAL, geometrySelftest);
#endif
    acedRegCmds->addCommand(kCommandGroup, _T("GAOPEN"), _T("GAOPEN"),
                            ACRX_CMD_MODAL, gaOpenInService);
    gaDelivery::installMenu(gaQueueOpenInService);
    acedRegCmds->addCommand(kCommandGroup, _T("GAEXPORTPROBE"),
                            _T("GAEXPORTPROBE"), ACRX_CMD_MODAL, exportProbe);
    acedRegCmds->addCommand(kCommandGroup, _T("GAEXPORTREGIONPROBE"),
                            _T("GAEXPORTREGIONPROBE"), ACRX_CMD_MODAL,
                            exportRegionTopologyProbe);
    acedRegCmds->addCommand(kCommandGroup, _T("GAEXPORTSNAPSHOTFILE"),
                            _T("GAEXPORTSNAPSHOTFILE"), ACRX_CMD_MODAL,
                            exportRegionTopologyFromSourceFile);
    acedRegisterOnIdleWinMsg(processMcpRequestsOnIdle);
    acedRegisterOnIdleWinMsg(ga::liveQuery::pollRequests);
    publishMcpStatus(true);
}

void unload() {
    acedRemoveOnIdleWinMsg(ga::liveQuery::pollRequests);
    ga::liveQuery::closeSession();
    gaDelivery::removeMenu();
    acedRemoveOnIdleWinMsg(processMcpRequestsOnIdle);
    publishMcpStatus(false);
    acedRegCmds->removeGroup(kCommandGroup);
}

}  // namespace ga::bridge
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode message, void* appId) {
    switch (message) {
    case AcRx::kInitAppMsg:
        acrxLoadModule(_T("AcGeomentObj.dbx"), 0);
        acrxDynamicLinker->loadModule(_T("AcBr.dbx"), 1);
        acrxDynamicLinker->unlockApplication(appId);
        acrxDynamicLinker->registerAppMDIAware(appId);
        ga::bridge::initialize();
        break;
    case AcRx::kUnloadAppMsg:
        ga::bridge::unload();
        acrxDynamicLinker->unloadModule(_T("AcBr.dbx"));
        acrxUnloadModule(_T("AcGeomentObj.dbx"));
        break;
    default:
        break;
    }
    return AcRx::kRetOK;
}
