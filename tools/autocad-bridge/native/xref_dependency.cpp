#include "xref_dependency.h"
#include "cad_utils.h"
#include "file_io.h"
#include "dbsymtb.h"
#include "dbapserv.h"
#include "AcString.h"

namespace ga::bridge {

XrefDependency inspectXrefDependency(AcDbBlockTableRecord* record) {
    XrefDependency dependency;
    dependency.recordHandle = objectHandle(record);
    AcString blockName;
    if (record->getName(blockName) == Acad::eOk) {
        dependency.blockName = utf8(blockName.kwszPtr());
    }
    AcString storedPath;
    if (record->pathName(storedPath) == Acad::eOk) {
        dependency.storedPath = utf8(storedPath.kwszPtr());
    }
    AcString resolvedPath;
    if (!dependency.storedPath.empty() &&
        acdbHostApplicationServices()->findFile(
            resolvedPath,
            storedPath.kwszPtr(),
            record->database(),
            AcDbHostApplicationServices::kXRefDrawing) == Acad::eOk &&
        !resolvedPath.isEmpty()) {
        dependency.resolvedPath = utf8(resolvedPath.kwszPtr());
        dependency.bytes = fileSize(dependency.resolvedPath);
        dependency.sha256 = sha256File(dependency.resolvedPath);
    }
    dependency.status =
        record->xrefStatus() == AcDb::kXrfResolved &&
        !dependency.resolvedPath.empty() && dependency.bytes > 0 &&
        !dependency.sha256.empty()
        ? "resolved"
        : "unresolved";
    return dependency;
}

}  // namespace ga::bridge
