#include "delivery_command.h"
#include "delivery_ui.h"
#include "delivery_recovery.h"
#include "reference_search.h"
#include "aced.h"
#include "adslib.h"
#include "acdocman.h"
#include "dbapserv.h"
#include "dbsymtb.h"
#include "acdbxref.h"
#include "AcString.h"

#include <map>
#include <memory>
#include <stdexcept>
#include <vector>

namespace {
bool preparing = false;
struct Preparing {
    Preparing() { preparing = true; }
    ~Preparing() { preparing = false; }
};
struct WorkingDatabase {
    AcDbDatabase* previous = acdbHostApplicationServices()->workingDatabase();
    explicit WorkingDatabase(AcDbDatabase* db) { acdbHostApplicationServices()->setWorkingDatabase(db); }
    ~WorkingDatabase() { acdbHostApplicationServices()->setWorkingDatabase(previous); }
};

template<class T> struct Close { void operator()(T* object) const { if (object) object->close(); } };
template<class T> using Opened = std::unique_ptr<T, Close<T>>;

void require(Acad::ErrorStatus status, const char* operation) {
    if (status != Acad::eOk)
        throw std::runtime_error(std::string(operation) + " (AutoCAD " + std::to_string(status) + ")");
}

struct Reference {
    AcDbObjectId id;
    std::string name;
    std::string resolvedPath;
    bool resolved;
    bool unloaded;
};
std::vector<Reference> references(AcDbDatabase* database) {
    AcDbBlockTable* tablePointer = nullptr;
    require(database->getBlockTable(tablePointer, AcDb::kForRead), "Не удалось прочитать подосновы");
    Opened<AcDbBlockTable> table(tablePointer);
    AcDbBlockTableIterator* iteratorPointer = nullptr;
    require(table->newIterator(iteratorPointer), "Не удалось перечислить подосновы");
    std::unique_ptr<AcDbBlockTableIterator> iterator(iteratorPointer);
    std::vector<Reference> result;
    for (; !iterator->done(); iterator->step()) {
        AcDbBlockTableRecord* recordPointer = nullptr;
        require(iterator->getRecord(recordPointer, AcDb::kForRead), "Не удалось прочитать ссылку");
        Opened<AcDbBlockTableRecord> record(recordPointer);
        if (!record->isFromExternalReference()) continue;
        AcString name;
        require(record->getName(name), "Не удалось прочитать имя ссылки");
        // xrefDatabase()->getFilename() can name AutoCAD's transient $0$.ac$
        // backing database. Resolve the authored path through the host instead,
        // exactly as the existing native evidence exporter does.
        AcString storedPath, resolvedPath;
        if (record->pathName(storedPath) == Acad::eOk && !storedPath.isEmpty())
            acdbHostApplicationServices()->findFile(resolvedPath, storedPath.kwszPtr(),
                record->database(), AcDbHostApplicationServices::kXRefDrawing);
        result.push_back({record->objectId(), name.utf8Str(),
            resolvedPath.utf8Str(),
            record->xrefStatus() == AcDb::kXrfResolved, record->isUnloaded()});
    }
    return result;
}

void checkCancel() {
    if (acedUsrBrk()) throw std::runtime_error("Подготовка отменена.");
}

void checkSourceState(AcApDocument* expectedDocument, AcDbDatabase* expectedDatabase,
                      const char* stage) {
    // GAOPEN is a synchronous editor command, so user edits cannot race this
    // preparation. DBMOD is not an integrity token: AutoCAD itself may change
    // it while serialising a DXF although the authored database is untouched.
    // Guard the identity that matters instead — the active editor document and
    // its live database. All writes below target an independent database copy.
    auto* current = acDocManager ? acDocManager->curDocument() : nullptr;
    if (current != expectedDocument || !current || current->database() != expectedDatabase)
        throw std::runtime_error(std::string("Подготовка остановлена: активный чертёж сменился на этапе ")
            + stage + ". Файлы передачи не отправлены.");
}
}

bool gaDeliveryActive() { return preparing; }

void gaQueueOpenInService() {
    if (preparing) return;
    if (acDocManager && acDocManager->curDocument())
        acDocManager->sendStringToExecute(acDocManager->curDocument(), _T("GAOPEN\n"), true, false, false);
    else gaDelivery::error("Откройте чертёж в AutoCAD.");
}

void gaOpenInService() {
    if (preparing) return;
    Preparing active;
    try {
        auto* document = acDocManager ? acDocManager->curDocument() : nullptr;
        AcDbDatabase* live = document ? document->database() : nullptr;
        if (!live) { gaDelivery::error("Откройте чертёж в AutoCAD."); return; }
        WorkingDatabase sourceContext(live);
        resbuf modification{};
        if (acedGetVar(_T("DBMOD"), &modification) != RTNORM)
            throw std::runtime_error("Не удалось проверить состояние исходного чертежа.");
        const bool modified = modification.resval.rint != 0;
        if (!gaDelivery::confirmPreparation(modified)) return;
        gaDelivery::stage("Создаём отдельную копию чертежа…");
        checkCancel();
        auto referenceEvidence = gaDelivery::captureReferences(live);
        std::vector<std::string> issues;
        std::vector<std::string> preparationNotices;
        std::vector<gaDelivery::ReferencePathRequest> missing;
        for (const auto& reference : referenceEvidence)
            if (!reference.available && !reference.unloaded && !reference.instances.empty())
                missing.push_back({reference.name, reference.storedPath});
        if (!missing.empty()) {
            gaDelivery::endStage();
            std::vector<gaDelivery::ReferencePathMatch> chosen;
            if (!gaDelivery::chooseReferencePaths(missing, chosen)) return;
            for (const auto& match : chosen) {
                for (auto& reference : referenceEvidence)
                    if (reference.name == match.name) {
                        reference.resolvedPath = match.path;
                        reference.available = true;
                    }
                preparationNotices.push_back(match.name + ": путь в копии выбран пользователем: " + match.path);
            }
            checkSourceState(document, live, "выбор подоснов");
            gaDelivery::stage("Создаём отдельную копию чертежа…");
        }
        const std::string directory = gaDelivery::createStaging();
        if (directory.empty()) throw std::runtime_error("Не удалось создать папку передачи.");
        // The whole-database WBLOCK path marked the live XREF document dirty
        // and produced broken block references in the copy on AutoCAD Mac 2027.
        // Serialize the CURRENT database (including unsaved edits), then read an
        // independent native database. Do not reopen the stale user file, share
        // live XREF object IDs, or bind in the user's document.
        const std::string sourceCopy = directory + "/.source.dxf";
        require(live->dxfOut(AcString(sourceCopy.c_str()).kwszPtr(), 16),
            "AutoCAD не смог создать DXF-копию чертежа");
        checkSourceState(document, live, "DXF-копия");
        auto copy = std::make_unique<AcDbDatabase>(false, true);
        require(copy->dxfIn(AcString(sourceCopy.c_str()).kwszPtr()),
            "AutoCAD не смог открыть отдельную DXF-копию");
        checkSourceState(document, live, "чтение копии");
        const std::string drawing = directory + "/Drawing.dxf";
        {
            WorkingDatabase working(copy.get());
            if (!gaDelivery::recoverReferences(copy.get(), referenceEvidence, preparationNotices)) {
                gaDelivery::endStage();
                return;
            }
            checkSourceState(document, live, "восстановление ссылок");
            // Resolved paths or explicit user-approved matches from the chosen
            // package only. Unloaded references stay unloaded by user choice.
            std::map<std::string, std::string> paths;
            for (const auto& reference : referenceEvidence)
                if (reference.available && !reference.unloaded && !reference.resolvedPath.empty())
                    paths[reference.name] = reference.resolvedPath;
            for (const auto& reference : references(copy.get())) {
                if (reference.id.database() != copy.get())
                    throw std::runtime_error("AutoCAD вернул ссылку вне подготовленной копии.");
                auto path = paths.find(reference.name);
                if (path == paths.end()) continue;
                AcDbBlockTableRecord* pointer = nullptr;
                require(acdbOpenObject(pointer, reference.id, AcDb::kForWrite), "Не удалось подготовить путь подосновы");
                Opened<AcDbBlockTableRecord> record(pointer);
                require(record->setPathName(AcString(path->second.c_str()).kwszPtr()), "Не удалось привязать подоснову в копии");
            }
            checkSourceState(document, live, "пути подоснов");
            gaDelivery::stage("Включаем доступные подосновы в копию…");
            checkCancel();
            const auto resolution = acdbResolveCurrentXRefs(copy.get(), false, false);
            if (resolution != Acad::eOk)
                issues.push_back("Не все подосновы загрузились (AutoCAD " + std::to_string(resolution) + ").");
            checkSourceState(document, live, "загрузка подоснов");
            AcDbObjectIdArray available;
            for (const auto& reference : references(copy.get()))
                if (reference.resolved && !reference.unloaded) available.append(reference.id);
            if (!available.isEmpty()) {
                const auto bound = acdbBindXrefs(copy.get(), available, false, true, true);
                checkSourceState(document, live, "включение подоснов");
                if (bound != Acad::eOk)
                    issues.push_back("Не все подосновы удалось включить в копию (AutoCAD " + std::to_string(bound) + ").");
            }
            // Inspect the actual outcome; a successful bind return alone is not coverage.
            for (const auto& reference : references(copy.get()))
                issues.push_back(reference.name + (reference.unloaded
                    ? ": подоснова отключена в AutoCAD."
                    : ": внешняя ссылка не включена в передаваемую копию."));
            checkCancel();
            gaDelivery::stage("Сохраняем DXF-копию…");
            require(copy->dxfOut(AcString(drawing.c_str()).kwszPtr(), 16), "Не удалось записать DXF-копию");
        }
        copy.reset(); // Release CAD memory before native extraction.
        gaDelivery::stage("Проверяем геометрию средствами AutoCAD…");
        checkCancel();
        std::string exportError;
        if (!gaExportPreparedSnapshot(drawing, exportError))
            throw std::runtime_error("Не удалось подготовить геометрию: " + exportError);
        checkSourceState(document, live, "проверка геометрии");
        gaDelivery::endStage();
        const auto geometryIssues = gaDelivery::probeIssues(directory);
        issues.insert(issues.end(), geometryIssues.begin(), geometryIssues.end());
        if (!issues.empty() && !gaDelivery::confirmPartial(issues)) return;
        // Recovery/skip was already a user decision. Preserve it in the package,
        // but do not show successful recovery as a second partial-data warning.
        issues.insert(issues.end(), preparationNotices.begin(), preparationNotices.end());
        const auto version = acdbHostApplicationServices()->releaseMarketVersion();
        const std::string ticket = gaDelivery::writeTicket(directory, "0.1.30",
            version ? AcString(version).utf8Str() : "2027", issues);
        if (ticket.empty()) throw std::runtime_error("Не удалось проверить файлы передачи.");
        if (!gaDelivery::launchConnector(ticket))
            throw std::runtime_error("Не удалось открыть приложение передачи. Переустановите полный пакет Green Atlas.");
        acutPrintf(_T("\nGreen Atlas: копия передана в локальное приложение."));
    } catch (const std::exception& failure) {
        gaDelivery::endStage();
        gaDelivery::error(failure.what());
    }
}
