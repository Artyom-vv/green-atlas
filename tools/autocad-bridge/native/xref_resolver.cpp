#include "xref_resolver.h"
#include "cad_utils.h"
#include "file_io.h"
#include "reference_search.h"
#include "dbapserv.h"
#include "AcString.h"
#include <algorithm>
#include <cctype>

namespace ga::bridge {

bool ExternalDatabaseCache::modelSpace(const std::string& path, AcDbObjectId& modelSpaceId,
                std::string& reason) {
    auto found = databases.find(path);
    if (found == databases.end()) {
        auto database = std::make_unique<AcDbDatabase>(false, true);
        std::string extension = path.size() >= 4
            ? path.substr(path.size() - 4) : std::string();
        std::transform(extension.begin(), extension.end(), extension.begin(),
                       [](const unsigned char value) {
                           return static_cast<char>(std::tolower(value));
                       });
        Acad::ErrorStatus status = Acad::eInvalidInput;
        if (extension == ".dwg") {
            status = database->readDwgFile(
                AcString(path.c_str()).kwszPtr(),
                AcDbDatabase::kForReadAndAllShare, true);
        } else if (extension == ".dxf") {
            const std::string logPath = path + ".green-atlas.xref.dxf.log";
            status = database->dxfIn(
                AcString(path.c_str()).kwszPtr(),
                AcString(logPath.c_str()).kwszPtr());
        }
        if (status != Acad::eOk) {
            reason = "AutoCAD could not open package-local XREF database "
                "(status=" + std::to_string(static_cast<int>(status)) + ")";
            return false;
        }
        found = databases.emplace(path, std::move(database)).first;
    }
    AcDbBlockTable* table = nullptr;
    const Acad::ErrorStatus tableStatus =
        found->second->getBlockTable(table, AcDb::kForRead);
    if (tableStatus != Acad::eOk || table == nullptr) {
        reason = "AutoCAD could not open package-local XREF block table";
        return false;
    }
    const Acad::ErrorStatus modelStatus =
        table->getAt(ACDB_MODEL_SPACE, modelSpaceId);
    table->close();
    if (modelStatus != Acad::eOk || modelSpaceId.isNull()) {
        reason = "AutoCAD could not open package-local XREF model space";
        return false;
    }
    return true;
}
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

AcDbObjectIdArray relinkUniquePackageLocalXrefs(AcDbDatabase* database,
                                                const std::string& sourcePath) {
    AcDbObjectIdArray relinked;
    AcDbBlockTable* tablePointer = nullptr;
    if (database->getBlockTable(tablePointer, AcDb::kForRead) != Acad::eOk ||
        tablePointer == nullptr) return relinked;
    std::unique_ptr<AcDbBlockTable, void(*)(AcDbBlockTable*)> table(
        tablePointer, [](AcDbBlockTable* value) { if (value) value->close(); });
    AcDbBlockTableIterator* iteratorPointer = nullptr;
    if (table->newIterator(iteratorPointer) != Acad::eOk || iteratorPointer == nullptr)
        return relinked;
    std::unique_ptr<AcDbBlockTableIterator> iterator(iteratorPointer);
    struct PendingReference {
        AcDbObjectId id;
        gaDelivery::ReferencePathRequest request;
    };
    std::vector<PendingReference> pending;
    for (; !iterator->done(); iterator->step()) {
        AcDbBlockTableRecord* recordPointer = nullptr;
        if (iterator->getRecord(recordPointer, AcDb::kForRead) != Acad::eOk ||
            recordPointer == nullptr) continue;
        std::unique_ptr<AcDbBlockTableRecord, void(*)(AcDbBlockTableRecord*)> record(
            recordPointer,
            [](AcDbBlockTableRecord* value) { if (value) value->close(); });
        if (!record->isFromExternalReference() || !record->isUnloaded()) continue;
        AcString name, storedPath;
        if (record->getName(name) != Acad::eOk ||
            record->pathName(storedPath) != Acad::eOk || storedPath.isEmpty()) continue;
        pending.push_back({record->objectId(), {utf8(name.kwszPtr()),
                                               utf8(storedPath.kwszPtr())}});
    }
    if (pending.empty()) return relinked;
    std::vector<gaDelivery::ReferencePathRequest> requests;
    requests.reserve(pending.size());
    for (const auto& item : pending) requests.push_back(item.request);
    std::vector<gaDelivery::ReferencePathMatch> matches;
    try {
        matches = gaDelivery::findReferencePaths(parentDirectory(sourcePath), requests);
    } catch (...) {
        return relinked;
    }
    for (std::size_t index = 0; index < pending.size() && index < matches.size(); ++index) {
        if (matches[index].path.empty()) continue;
        AcDbBlockTableRecord* recordPointer = nullptr;
        if (acdbOpenObject(recordPointer, pending[index].id, AcDb::kForWrite) != Acad::eOk ||
            recordPointer == nullptr) continue;
        if (recordPointer->setPathName(
                AcString(matches[index].path.c_str()).kwszPtr()) == Acad::eOk) {
            relinked.append(pending[index].id);
        }
        recordPointer->close();
    }
    return relinked;
}

}  // namespace ga::bridge
