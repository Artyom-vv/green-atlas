#include "capture_commands.h"
#include "bridge_config.h"
#include "cad_utils.h"
#include "file_io.h"
#include "aced.h"
#include "adslib.h"
#include "dbapserv.h"
#include "dbsymtb.h"
#include "dbregion.h"
#include "AcString.h"
#include <cstdio>
#include <fstream>
#include <iomanip>

namespace ga::bridge {

void exportProbe() {
    AcDbDatabase* database = acdbHostApplicationServices()->workingDatabase();
    if (database == nullptr) {
        acutPrintf(_T("\nGreen Atlas: no active drawing."));
        return;
    }

    resbuf databaseModification = {};
    const bool databaseModificationKnown =
        acedGetVar(_T("DBMOD"), &databaseModification) == RTNORM &&
        databaseModification.restype == RTSHORT;
    const int databaseModificationFlags =
        databaseModificationKnown ? databaseModification.resval.rint : -1;
    if (!databaseModificationKnown || databaseModificationFlags != 0) {
        const AcString databaseModificationText(
            databaseModificationKnown ? std::to_string(databaseModificationFlags).c_str() : "unknown");
        acutPrintf(
            _T("\nGreen Atlas: the live drawing has unsaved changes (DBMOD=%s); "
               "reopen or save a deliberate copy before exporting."),
            databaseModificationText.kwszPtr());
        return;
    }

    const ACHAR* sourceName = nullptr;
    if (database->getFilename(sourceName) != Acad::eOk || sourceName == nullptr || *sourceName == 0) {
        acutPrintf(_T("\nGreen Atlas: save the drawing before exporting a probe."));
        return;
    }

    const std::string sourcePath = utf8(sourceName);
    const std::string sourceHash = sha256File(sourcePath);
    if (sourceHash.empty()) {
        acutPrintf(_T("\nGreen Atlas: cannot read the saved source file."));
        return;
    }

    AcDbBlockTable* blockTable = nullptr;
    if (database->getBlockTable(blockTable, AcDb::kForRead) != Acad::eOk) {
        acutPrintf(_T("\nGreen Atlas: cannot open the block table."));
        return;
    }

    AcDbBlockTableRecord* modelSpace = nullptr;
    const Acad::ErrorStatus modelStatus =
        blockTable->getAt(ACDB_MODEL_SPACE, modelSpace, AcDb::kForRead);
    blockTable->close();
    if (modelStatus != Acad::eOk || modelSpace == nullptr) {
        acutPrintf(_T("\nGreen Atlas: cannot open model space."));
        return;
    }

    AcDbBlockTableRecordIterator* iterator = nullptr;
    if (modelSpace->newIterator(iterator) != Acad::eOk || iterator == nullptr) {
        modelSpace->close();
        acutPrintf(_T("\nGreen Atlas: cannot enumerate model space."));
        return;
    }

    std::map<std::string, std::size_t> classes;
    std::vector<RegionMeasurement> regions;
    std::size_t entityCount = 0;

    for (iterator->start(); !iterator->done(); iterator->step()) {
        AcDbEntity* entity = nullptr;
        if (iterator->getEntity(entity, AcDb::kForRead) != Acad::eOk || entity == nullptr) {
            continue;
        }

        ++entityCount;
        ++classes[utf8(entity->isA()->name())];

        if (AcDbRegion* region = AcDbRegion::cast(entity)) {
            RegionMeasurement measurement;
            measurement.handle = entityHandle(region);
            measurement.layer = utf8(region->layer());
            measurement.areaValid = region->getArea(measurement.area) == Acad::eOk;
            measurement.perimeterValid = region->getPerimeter(measurement.perimeter) == Acad::eOk;
            regions.push_back(std::move(measurement));
        }

        entity->close();
    }

    delete iterator;
    modelSpace->close();

    AcString revision;
    database->getVersionGuid(revision);
    const std::string outputPath = sourcePath + ".green-atlas.probe.json";
    const std::string temporaryPath = outputPath + ".tmp";

    std::ofstream output(temporaryPath, std::ios::binary | std::ios::trunc);
    if (!output) {
        acutPrintf(_T("\nGreen Atlas: cannot create the sidecar file."));
        return;
    }
    output << std::setprecision(17);
    output << "{\n"
           << "  \"schema\": \"green-atlas.autocad-probe/1\",\n"
           << "  \"complete\": false,\n"
           << "  \"plugin_version\": \"" << kPluginVersion << "\",\n"
           << "  \"source\": {\n"
           << "    \"path\": \"" << jsonEscape(sourcePath) << "\",\n"
           << "    \"sha256\": \"" << sourceHash << "\",\n"
           << "    \"units_code\": " << static_cast<int>(database->insunits()) << ",\n"
           << "    \"document_revision\": \"" << jsonEscape(revision.utf8Str()) << "\",\n"
           << "    \"database_modified_flags\": ";
    if (databaseModificationKnown) output << databaseModificationFlags; else output << "null";
    output << ",\n"
           << "    \"live_database_matches_disk\": ";
    if (!databaseModificationKnown) output << "null";
    else output << (databaseModificationFlags == 0 ? "true" : "null");
    output << "\n"
           << "  },\n"
           << "  \"model_space_entities\": " << entityCount << ",\n"
           << "  \"classes\": {";

    bool first = true;
    for (const auto& [name, count] : classes) {
        output << (first ? "\n" : ",\n")
               << "    \"" << jsonEscape(name) << "\": " << count;
        first = false;
    }
    if (!classes.empty()) {
        output << '\n';
    }
    output << "  },\n  \"regions\": [";

    for (std::size_t index = 0; index < regions.size(); ++index) {
        const RegionMeasurement& region = regions[index];
        output << (index == 0 ? "\n" : ",\n")
               << "    {\"handle\": \"" << jsonEscape(region.handle)
               << "\", \"layer\": \"" << jsonEscape(region.layer) << "\", "
               << "\"area\": ";
        if (region.areaValid) output << region.area; else output << "null";
        output << ", \"perimeter\": ";
        if (region.perimeterValid) output << region.perimeter; else output << "null";
        output << '}';
    }
    if (!regions.empty()) {
        output << '\n';
    }
    output << "  ],\n"
           << "  \"limitations\": [\"model-space roots only\", "
              "\"nested INSERT/XREF instances not expanded\", "
              "\"REGION topology not tessellated yet\", "
              "\"nonzero DBMOD can be intrinsic to opening DXF and does not identify user edits\"]\n"
           << "}\n";
    output.close();

    if (!output || std::rename(temporaryPath.c_str(), outputPath.c_str()) != 0) {
        std::remove(temporaryPath.c_str());
        acutPrintf(_T("\nGreen Atlas: failed to publish the sidecar atomically."));
        return;
    }

    const AcString entityCountText(std::to_string(entityCount).c_str());
    const AcString regionCountText(std::to_string(regions.size()).c_str());
    const AcString databaseModificationText(
        databaseModificationKnown ? std::to_string(databaseModificationFlags).c_str() : "unknown");
    acutPrintf(_T("\nGreen Atlas: probe exported (%s entities, %s regions, DBMOD=%s)."),
               entityCountText.kwszPtr(), regionCountText.kwszPtr(),
               databaseModificationText.kwszPtr());
    acutPrintf(_T("\n%s"), AcString(outputPath.c_str()).kwszPtr());
}

}  // namespace ga::bridge
