#include "rxregsvc.h"
#include "aced.h"
#include "core_rxmfcapi.h"
#include "native_query_command.h"
#include "package_query.h"

// Background worker loads the SAME native code as the editor plugin, but no
// AppKit menu, global GUI MCP queue, idle reactor or delivery side effects.
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode message, void* appId) {
    if(message==AcRx::kInitAppMsg) {
        acrxLoadModule(_T("AcGeomentObj.dbx"),0);
        acrxDynamicLinker->loadModule(_T("AcBr.dbx"),1);
        acrxDynamicLinker->unlockApplication(appId);
        acrxDynamicLinker->registerAppMDIAware(appId);
        acedRegCmds->addCommand(_T("GREEN_ATLAS_QUERY"),_T("GAQUERYOBJECTS"),
            _T("GAQUERYOBJECTS"),ACRX_CMD_MODAL,ga::nativeQuery::queryObjectsCommand);
        acedRegCmds->addCommand(_T("GREEN_ATLAS_QUERY"),_T("GAQUERYPACKAGE"),
            _T("GAQUERYPACKAGE"),ACRX_CMD_MODAL,ga::queryWorker::queryPackageCommand);
    } else if(message==AcRx::kUnloadAppMsg) {
        acedRegCmds->removeGroup(_T("GREEN_ATLAS_QUERY"));
    }
    return AcRx::kRetOK;
}
