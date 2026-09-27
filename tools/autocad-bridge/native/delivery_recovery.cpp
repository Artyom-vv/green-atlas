#include "delivery_recovery.h"
#include "delivery_ui.h"
#include "dbsymtb.h"
#include "dbents.h"
#include "dbapserv.h"
#include "acdbxref.h"
#include "AcString.h"
#include <memory>
#include <stdexcept>

namespace {
template<class T> struct Close { void operator()(T* p) const { if (p) p->close(); } };
template<class T> using Opened = std::unique_ptr<T, Close<T>>;
void require(Acad::ErrorStatus status, const char* message) {
    if (status != Acad::eOk)
        throw std::runtime_error(std::string(message) + " (AutoCAD " + std::to_string(status) + ")");
}
}
namespace gaDelivery {
std::vector<ReferenceEvidence> captureReferences(AcDbDatabase* source) {
    AcDbBlockTable* raw = nullptr;
    require(source->getBlockTable(raw, AcDb::kForRead), "Не удалось прочитать ссылки исходника");
    Opened<AcDbBlockTable> table(raw);
    AcDbBlockTableIterator* rawIterator = nullptr;
    require(table->newIterator(rawIterator), "Не удалось перечислить блоки исходника");
    std::unique_ptr<AcDbBlockTableIterator> blocks(rawIterator);
    std::vector<ReferenceEvidence> result;
    std::vector<AcDbObjectId> definitions;
    for (; !blocks->done(); blocks->step()) {
        AcDbBlockTableRecord* pointer = nullptr;
        require(blocks->getRecord(pointer, AcDb::kForRead), "Не удалось прочитать блок исходника");
        Opened<AcDbBlockTableRecord> block(pointer);
        if (!block->isFromExternalReference()) continue;
        AcString name, stored, resolved;
        require(block->getName(name), "Не удалось прочитать имя подосновы");
        if (block->pathName(stored) == Acad::eOk && !stored.isEmpty())
            acdbHostApplicationServices()->findFile(resolved, stored.kwszPtr(), source,
                AcDbHostApplicationServices::kXRefDrawing);
        result.push_back({name.utf8Str(), resolved.utf8Str(),
            !resolved.isEmpty() && block->xrefStatus() == AcDb::kXrfResolved && !block->isUnloaded(),
            block->isFromOverlayReference(), {}, stored.utf8Str(), block->isUnloaded()});
        definitions.push_back(block->objectId());
    }
    blocks->start();
    for (; !blocks->done(); blocks->step()) {
        AcDbBlockTableRecord* pointer = nullptr;
        require(blocks->getRecord(pointer, AcDb::kForRead), "Не удалось прочитать контейнер исходника");
        Opened<AcDbBlockTableRecord> block(pointer);
        if (block->isFromExternalReference() || block->isDependent()) continue;
        AcDbBlockTableRecordIterator* rawEntities = nullptr;
        require(block->newIterator(rawEntities), "Не удалось перечислить вставки исходника");
        std::unique_ptr<AcDbBlockTableRecordIterator> entities(rawEntities);
        for (; !entities->done(); entities->step()) {
            AcDbEntity* entityPointer = nullptr;
            require(entities->getEntity(entityPointer, AcDb::kForRead), "Не удалось прочитать вставку исходника");
            Opened<AcDbEntity> entity(entityPointer);
            auto* insert = AcDbBlockReference::cast(entity.get());
            if (!insert) continue;
            for (size_t i = 0; i < definitions.size(); ++i) {
                if (insert->blockTableRecord() != definitions[i]) continue;
                AcDbHandle handle;
                insert->getAcDbHandle(handle);
                if (handle.isNull()) throw std::runtime_error("Не удалось идентифицировать вставку.");
                result[i].instances.push_back(handle);
                break;
            }
        }
    }
    return result;
}

bool recoverReferences(AcDbDatabase* copy, const std::vector<ReferenceEvidence>& source,
                       std::vector<std::string>& notices) {
    std::vector<const ReferenceEvidence*> lost;
    {
        AcDbBlockTable* pointer = nullptr;
        require(copy->getBlockTable(pointer, AcDb::kForRead), "Не удалось проверить ссылки копии");
        Opened<AcDbBlockTable> table(pointer);
        for (const auto& reference : source)
            if (!reference.instances.empty() && !table->has(AcString(reference.name.c_str()).kwszPtr()))
                lost.push_back(&reference);
    }
    if (lost.empty()) return true;
    size_t available = 0;
    for (const auto* reference : lost) if (reference->available) ++available;
    endStage();
    const auto choice = confirmReferenceRecovery(lost.size(), available);
    if (choice == ReferenceRecovery::Cancel) return false;
    stage("Подготавливаем подосновы в копии…");
    for (const auto* reference : lost) {
        // Validate every target before attaching anything. A same-handle object
        // of another type or a valid block reference must never be overwritten.
        AcDbObjectIdArray targets;
        for (const auto& handle : reference->instances) {
            AcDbObjectId id;
            require(copy->getAcDbObjectId(id, false, handle), "Не найдена вставка в копии");
            if (id.database() != copy) throw std::runtime_error("Вставка не принадлежит копии.");
            AcDbBlockReference* pointer = nullptr;
            require(acdbOpenObject(pointer, id, AcDb::kForRead), "Не удалось проверить вставку копии");
            Opened<AcDbBlockReference> insert(pointer);
            if (!insert->blockTableRecord().isNull())
                throw std::runtime_error("Вставка копии уже связана с другим блоком. Автоматическая замена отменена.");
            targets.append(id);
        }
        AcDbObjectId restored;
        const bool restore = choice == ReferenceRecovery::RestoreAvailable && reference->available;
        if (restore) {
            const auto path = AcString(reference->resolvedPath.c_str());
            const auto name = AcString(reference->name.c_str());
            require(reference->overlay ? acdbOverlayXref(copy, path.kwszPtr(), name.kwszPtr(), restored)
                                       : acdbAttachXref(copy, path.kwszPtr(), name.kwszPtr(), restored),
                "AutoCAD не смог восстановить подоснову в копии");
        }
        for (int i = 0; i < targets.length(); ++i) {
            AcDbBlockReference* pointer = nullptr;
            require(acdbOpenObject(pointer, targets[i], AcDb::kForWrite), "Не удалось открыть вставку копии");
            Opened<AcDbBlockReference> insert(pointer);
            require(restore ? insert->setBlockTableRecord(restored) : insert->erase(),
                "Не удалось обновить ссылку в копии");
        }
        notices.push_back(reference->name + (restore
            ? ": определение ссылки восстановлено в копии по подтверждению пользователя."
            : ": подоснова пропущена в копии по подтверждению пользователя."));
    }
    return true;
}
}
