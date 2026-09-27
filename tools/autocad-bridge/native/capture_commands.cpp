#include "capture_commands.h"
#include "cad_utils.h"
#include "file_io.h"
#include "bridge_config.h"
#include "drawing_traversal.h"
#include "xref_resolver.h"
#include "operation_control.h"
#include "mcp_transport.h"
#include "aced.h"
#include "adslib.h"
#include "dbapserv.h"
#include "gemat3d.h"
#include "AcString.h"
#include <algorithm>
#include <cctype>

namespace ga::bridge {

namespace {
bool gSideDatabaseCapture = false;

ExportResult captureTopology(AcDbDatabase* database,
                             const std::string& capturedSourcePath = {},
                             const std::string& destination = {},
                             const CaptureObserver& observer = {}) {
    const bool sideDatabaseCapture = !capturedSourcePath.empty();
    ExportResult result;
    result.success = false;
    result.path.clear();
    result.error = "native topology export did not complete";
    if (database == nullptr) {
        result.error = "no active AutoCAD database";
        acutPrintf(_T("\nGreen Atlas: no active drawing."));
        return result;
    }

    resbuf databaseModification = {};
    const bool databaseModificationKnown = sideDatabaseCapture ||
        (acedGetVar(_T("DBMOD"), &databaseModification) == RTNORM &&
         databaseModification.restype == RTSHORT);
    const int databaseModificationFlags = sideDatabaseCapture
        ? 0
        : (databaseModificationKnown ? databaseModification.resval.rint : -1);
    if (!databaseModificationKnown) {
        result.error = "DBMOD is unavailable";
        acutPrintf(_T("\nGreen Atlas: DBMOD is unavailable; topology provenance cannot be recorded."));
        return result;
    }

    const ACHAR* sourceName = nullptr;
    if (!sideDatabaseCapture &&
        (database->getFilename(sourceName) != Acad::eOk ||
         sourceName == nullptr || *sourceName == 0)) {
        result.error = "drawing must be saved before export";
        acutPrintf(_T("\nGreen Atlas: save the drawing before exporting topology."));
        return result;
    }
    const std::string sourcePath = sideDatabaseCapture
        ? capturedSourcePath
        : utf8(sourceName);
    const std::string sourceHash = sha256File(sourcePath);
    if (sourceHash.empty()) {
        result.error = "saved source file cannot be read";
        acutPrintf(_T("\nGreen Atlas: cannot read the saved source file."));
        return result;
    }

    const double metresPerUnit = unitToMetres(database->insunits());
    if (metresPerUnit <= 0.0) {
        result.error = "drawing units are undefined";
        acutPrintf(_T("\nGreen Atlas: drawing units are undefined; topology tolerance cannot be proven."));
        return result;
    }
    const double toleranceUnits = kRequestedToleranceMetres / metresPerUnit;

    AcDbBlockTable* blockTable = nullptr;
    if (database->getBlockTable(blockTable, AcDb::kForRead) != Acad::eOk) {
        result.error = "block table cannot be opened";
        acutPrintf(_T("\nGreen Atlas: cannot open the block table."));
        return result;
    }
    AcDbObjectId modelSpaceId;
    const Acad::ErrorStatus modelStatus =
        blockTable->getAt(ACDB_MODEL_SPACE, modelSpaceId);
    blockTable->close();
    if (modelStatus != Acad::eOk || modelSpaceId.isNull()) {
        result.error = "model space cannot be opened";
        acutPrintf(_T("\nGreen Atlas: cannot open model space."));
        return result;
    }

    DrawingGeometry geometry;
    auto& regions = geometry.regions;
    auto& areaProposals = geometry.areaProposals;
    auto& paths = geometry.paths;
    auto& points = geometry.points;
    auto& coverage = geometry.coverage;
    ExternalDatabaseCache externalDatabases;
    auto& traversal = geometry.traversal;
    const AcGeMatrix3d rootTransform = AcGeMatrix3d::kIdentity;
    std::vector<std::string> instanceChain;
    std::vector<AcDbObjectId> activeRecords;
    collectRegionInstances(modelSpaceId, rootTransform, instanceChain, "0", "",
                           toleranceUnits, activeRecords, regions, areaProposals, paths, points,
                           coverage, externalDatabases, traversal);
    if (operationCancelled()) {
        result.error = "native topology export cancelled";
        return result;
    }
    if (observer) observer(geometry);
    reportOperationProgress("serializing", traversal.visitedEntities);

    AcString revision;
    database->getVersionGuid(revision);
    SnapshotMetadata metadata{sourcePath, sourceHash, revision.utf8Str(),
        static_cast<int>(database->insunits()), metresPerUnit,
        databaseModificationFlags, sideDatabaseCapture};
    result = writeTopologySnapshot(metadata, geometry, destination);
    if (!result.success) {
        acutPrintf(_T("\nGreen Atlas: %s"), AcString(result.error.c_str()).kwszPtr());
        return result;
    }
    const auto resolvedCount = std::count_if(regions.begin(), regions.end(),
        [](const RegionTopology& region) { return region.resolved; });
    const auto& outputPath = result.path;
    const AcString regionCountText(std::to_string(regions.size()).c_str());
    const AcString resolvedCountText(std::to_string(resolvedCount).c_str());
    acutPrintf(_T("\nGreen Atlas: REGION topology probe exported (%s/%s resolved)."),
               resolvedCountText.kwszPtr(), regionCountText.kwszPtr());
    if (!sideDatabaseCapture && databaseModificationFlags != 0) {
        const AcString databaseModificationText(
            std::to_string(databaseModificationFlags).c_str());
        acutPrintf(_T("\nGreen Atlas: diagnostic-only snapshot (DBMOD=%s)."),
                   databaseModificationText.kwszPtr());
    }
    acutPrintf(_T("\n%s"), AcString(outputPath.c_str()).kwszPtr());
    return result;
}
}  // namespace

void exportRegionTopologyProbe() {
    captureTopology(acdbHostApplicationServices()->workingDatabase());
}

bool captureLiveSnapshotForService(const std::string& destination, std::string& error,
                                   const CaptureObserver& observer) {
    if (destination.empty() || mcpRequestInProgress() || gSideDatabaseCapture) {
        error = "AutoCAD уже подготавливает другой снимок или путь передачи пуст.";
        return false;
    }
    ScopedOperationControl control({[] { return acedUsrBrk() != 0; }, {}});
    const auto result = captureTopology(
        acdbHostApplicationServices()->workingDatabase(), {}, destination, observer);
    error = result.error;
    return result.success;
}

ExportResult exportRegionTopologyFromPath(const std::string& sourcePath) {
    ExportResult result;
    result.path.clear();
    result.error.clear();
    std::string extension = sourcePath.size() >= 4
        ? sourcePath.substr(sourcePath.size() - 4) : std::string();
    std::transform(extension.begin(), extension.end(), extension.begin(),
                   [](const unsigned char value) {
                       return static_cast<char>(std::tolower(value));
                   });
    const bool isDxf = extension == ".dxf";
    const bool isDwg = extension == ".dwg";
    if (!isDxf && !isDwg) {
        result.error = "only saved DWG or DXF sources are accepted";
        acutPrintf(_T("\nGreen Atlas: file-based topology export accepts saved DWG or DXF only."));
        return result;
    }

    AcDbDatabase sourceDatabase(false, true);
    Acad::ErrorStatus importStatus = Acad::eOk;
    if (isDxf) {
        const std::string logPath = sourcePath + ".green-atlas.dxf.log";
        importStatus = sourceDatabase.dxfIn(
            AcString(sourcePath.c_str()).kwszPtr(),
            AcString(logPath.c_str()).kwszPtr());
    } else {
        importStatus = sourceDatabase.readDwgFile(
            AcString(sourcePath.c_str()).kwszPtr(),
            AcDbDatabase::kForReadAndAllShare,
            true);
    }
    if (importStatus != Acad::eOk) {
        result.error =
            "isolated AutoCAD drawing import failed with status " +
            std::to_string(static_cast<int>(importStatus));
        const AcString statusText(std::to_string(static_cast<int>(importStatus)).c_str());
        acutPrintf(_T("\nGreen Atlas: isolated drawing import failed (status=%s)."),
                   statusText.kwszPtr());
        return result;
    }

    AcDbHostApplicationServices* services = acdbHostApplicationServices();
    AcDbDatabase* previousDatabase = services->workingDatabase();
    struct RestoreCapture {
        AcDbHostApplicationServices* services;
        AcDbDatabase* previous;
        ~RestoreCapture() {
            gSideDatabaseCapture = false;
            services->setWorkingDatabase(previous);
        }
    } restore{services, previousDatabase};
    services->setWorkingDatabase(&sourceDatabase);

    // Repair only deterministic package-local references in the isolated
    // database. Unique basenames, exact relative suffixes and byte-identical
    // duplicates are accepted; ambiguous different-content files stay
    // unresolved for explicit user review.
    const AcDbObjectIdArray packageLocalXrefs =
        relinkUniquePackageLocalXrefs(&sourceDatabase, sourcePath);

    // Do not call acdbResolveCurrentXRefs for a side database in AutoCAD for
    // Mac. It enters document-only interaction setup and can wait forever.
    // collectRegionInstances opens every deterministic package-local XREF as
    // its own read-only AcDbDatabase and applies the authored block transform.
    (void)packageLocalXrefs;

    gSideDatabaseCapture = true;
    return captureTopology(&sourceDatabase, sourcePath);
}

void exportRegionTopologyFromSourceFile() {
    AcDbDatabase* liveDatabase = acdbHostApplicationServices()->workingDatabase();
    if (liveDatabase == nullptr) {
        acutPrintf(_T("\nGreen Atlas: no active drawing."));
        return;
    }
    const ACHAR* sourceName = nullptr;
    if (liveDatabase->getFilename(sourceName) != Acad::eOk ||
        sourceName == nullptr || *sourceName == 0) {
        acutPrintf(_T("\nGreen Atlas: open a saved DWG or DXF before file-based export."));
        return;
    }
    exportRegionTopologyFromPath(utf8(sourceName));
}

}  // namespace ga::bridge
bool gaExportPreparedSnapshot(const std::string& path, std::string& error) {
    if (ga::bridge::mcpRequestInProgress() || ga::bridge::gSideDatabaseCapture) {
        error = "AutoCAD уже подготавливает другой снимок. Повторите после завершения.";
        return false;
    }
    const auto result = ga::bridge::exportRegionTopologyFromPath(path);
    error = result.error;
    return result.success;
}
