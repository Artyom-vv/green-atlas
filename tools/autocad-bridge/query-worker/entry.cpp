#include "rxregsvc.h"
#include "aced.h"
#include "core_rxmfcapi.h"
#include "native_query_command.h"
#include "package_query.h"
#include "package_faces.h"
#include "cad_release.h"
#include "runtime_modules.h"

// Фоновый работник использует расчётные исходники плагина без меню,
// глобальной очереди GUI, idle reactor и побочных действий передачи.
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode message, void* appId) {
    if(message==AcRx::kInitAppMsg) {
        acrxLoadModule(ga::bridge::kGeometryModule,0);
        acrxDynamicLinker->loadModule(ga::bridge::kBrepModule,1);
        acrxDynamicLinker->unlockApplication(appId);
        acrxDynamicLinker->registerAppMDIAware(appId);
        acedRegCmds->addCommand(_T("GREEN_ATLAS_QUERY"),_T("GAQUERYOBJECTS"),
            _T("GAQUERYOBJECTS"),ACRX_CMD_MODAL,ga::nativeQuery::queryObjectsCommand);
        acedRegCmds->addCommand(_T("GREEN_ATLAS_QUERY"),_T("GAQUERYPACKAGE"),
            _T("GAQUERYPACKAGE"),ACRX_CMD_MODAL,ga::queryWorker::queryPackageCommand);
        acedRegCmds->addCommand(_T("GREEN_ATLAS_QUERY"),_T("GAFACESPACKAGE"),
            _T("GAFACESPACKAGE"),ACRX_CMD_MODAL,ga::queryWorker::prepareFacesPackageCommand);
        acedRegCmds->addCommand(_T("GREEN_ATLAS_QUERY"),_T("GARELEASEPACKAGE"),
            _T("GARELEASEPACKAGE"),ACRX_CMD_MODAL,ga::queryWorker::releasePackageCommand);
    } else if(message==AcRx::kUnloadAppMsg) {
        acedRegCmds->removeGroup(_T("GREEN_ATLAS_QUERY"));
    }
    return AcRx::kRetOK;
}
