#include "session_capture_internal.h"
#include "session_capture.h"
#include "semantic_capture.h"
#include "instance_layer.h"
#include "xref_dependency.h"
#include "dbsymutl.h"
#include "aced.h"
#include "operation_control.h"
#include <algorithm>
#include <iomanip>
#include <set>
#include <sstream>

namespace ga::capture {
namespace {
constexpr std::size_t kMaximumInstanceDepth = 64;

struct InstanceWalk {
    std::vector<Instance>* rows;
    std::set<AcDbObjectId> verifiedXrefs;
    SemanticReadBudget* semanticBudget = nullptr;
    bool preflight() const { return rows == nullptr; }
};

[[noreturn]] void refuseLiveInstance(const std::string& route,
                                     const std::string& type,
                                     const std::string& reason,
                                     const std::string& explanation) {
    throw std::runtime_error("Передача отклонена: route=" + route + "; class=" +
        type + "; reason=" + reason + ". " + explanation);
}

void attributes(AcDbBlockReference& reference, const AcGeMatrix3d& transform,
                std::vector<AcDbObjectId>& chain, const std::string& route,
                const std::string& inheritedLayer, InstanceWalk& state) {
    std::unique_ptr<AcDbObjectIterator> it(reference.attributeIterator());
    if (!it) return;
    it->start();
    if (!state.preflight() && !it->done() && chain.size() >= kMaximumInstanceDepth)
        throw std::runtime_error("capture attribute exceeds instance depth: " + route);
    for (; !it->done(); it->step()) {
        if (acedUsrBrk()) throw std::runtime_error("Захват отменён");
        const auto id = it->objectId();
        auto attribute = open<AcDbAttribute>(id);
        const auto address = route + handle(id);
        const std::string type = AcString(attribute->isA()->name()).utf8Str();
        if (state.preflight() && chain.size() >= kMaximumInstanceDepth)
            refuseLiveInstance(address, type, "instance_depth_limit",
                "Адрес экземпляра превышает 64 элемента.");
        if (attribute->ownerId() != reference.objectId())
            throw std::runtime_error("capture attribute owner changed");
        AcString layer;
        checked(attribute->layer(layer), "capture attribute layer");
        chain.push_back(id);
        // An attached attribute is positioned in the block reference owner's
        // coordinates, not in the referenced definition's local coordinates.
        if (state.rows) {
            state.rows->push_back({address, type, layer.utf8Str(),
                ga::bridge::resolveInstanceLayer(layer.utf8Str(), inheritedLayer, ""),
                {}, chain, transform});
            if (state.rows->size() % 10000 == 0)
                ga::bridge::reportOperationProgress("archive_instances", state.rows->size());
            if (state.semanticBudget) {
                auto& row = state.rows->back();
                row.semanticEligible = semanticEligible(*attribute);
                row.semantic = readSemanticValue(*attribute, transform, *state.semanticBudget);
            }
        }
        chain.pop_back();
    }
}

void walk(AcDbObjectId recordId, const AcGeMatrix3d& transform,
          std::vector<AcDbObjectId>& chain, const std::string& route,
          const std::string& inheritedLayer, std::vector<AcDbObjectId>& active,
          InstanceWalk& state) {
    auto record = open<AcDbBlockTableRecord>(recordId);
    active.push_back(recordId);
    AcDbBlockTableRecordIterator* raw = nullptr;
    checked(record->newIterator(raw), "capture instance iterator");
    std::unique_ptr<AcDbBlockTableRecordIterator> it(raw);
    for (; !it->done(); it->step()) {
        if (acedUsrBrk()) throw std::runtime_error("Захват отменён");
        AcDbObjectId id;
        checked(it->getEntityId(id), "capture instance id");
        auto entity = open<AcDbEntity>(id);
        const auto address = route + handle(id);
        chain.push_back(id);
        const std::string type = AcString(entity->isA()->name()).utf8Str();
        if (state.preflight() && chain.size() > kMaximumInstanceDepth)
            refuseLiveInstance(address, type, "instance_depth_limit",
                "Адрес экземпляра превышает 64 элемента.");
        AcString layer;
        checked(entity->layer(layer), "capture layer");
        const auto effectiveLayer = ga::bridge::resolveInstanceLayer(
            layer.utf8Str(), inheritedLayer, "");
        std::string unavailable;
        auto* reference = AcDbBlockReference::cast(entity.get());
        bool descend = reference != nullptr;
        if (reference) {
            auto child = open<AcDbBlockTableRecord>(reference->blockTableRecord());
            if (state.preflight() && AcDbMInsertBlock::cast(reference))
                refuseLiveInstance(address, type, "array_instance",
                    "Массив MINSERT пока не переносится в архив проекта.");
            if (state.preflight() && child->isFromExternalReference() &&
                child->xrefStatus() == AcDb::kXrfResolved && child->xrefDatabase() &&
                !state.verifiedXrefs.count(child->objectId())) {
                const auto dependency = ga::bridge::inspectXrefDependency(child.get());
                if (dependency.status != "resolved")
                    refuseLiveInstance(address, type, "loaded_xref_disk_provenance_unavailable",
                        "Подоснова загружена в AutoCAD, но исходный файл недоступен "
                        "для проверки происхождения: " + dependency.storedPath);
                state.verifiedXrefs.insert(child->objectId());
            }
            if (AcDbMInsertBlock::cast(reference)) unavailable = "array_instance";
            else if (child->isFromExternalReference() &&
                     (child->xrefStatus() != AcDb::kXrfResolved || !child->xrefDatabase()))
                unavailable = "xref_not_loaded";
            else if (std::find(active.begin(), active.end(), child->objectId()) != active.end())
                unavailable = "cyclic_block";
            else if (!state.preflight() && chain.size() >= kMaximumInstanceDepth)
                unavailable = "instance_depth_limit";
            descend = unavailable.empty();
        }
        const bool collectAttributes = reference && unavailable != "cyclic_block" &&
            !AcDbMInsertBlock::cast(reference);
        if (state.rows) {
            state.rows->push_back({address, type, layer.utf8Str(), effectiveLayer,
                                  unavailable, chain, transform});
            if (state.rows->size() % 10000 == 0)
                ga::bridge::reportOperationProgress("archive_instances", state.rows->size());
            if (state.semanticBudget) {
                auto& row = state.rows->back();
                row.semanticEligible = semanticEligible(*entity);
                row.semantic = readSemanticValue(*entity, transform, *state.semanticBudget);
            }
        }
        if (collectAttributes)
            attributes(*reference, transform, chain, address + "/", effectiveLayer, state);
        if (descend)
            walk(reference->blockTableRecord(), transform * reference->blockTransform(),
                 chain, address + "/", effectiveLayer, active, state);
        chain.pop_back();
    }
    active.pop_back();
}
}

std::vector<Instance> captureInstances(AcDbDatabase& source, bool withSemantics) {
    std::vector<Instance> rows;
    std::vector<AcDbObjectId> chain, active;
    SemanticReadBudget budget;
    InstanceWalk state{&rows, {}, withSemantics ? &budget : nullptr};
    walk(acdbSymUtil()->blockModelSpaceId(&source), AcGeMatrix3d::kIdentity,
         chain, "", "0", active, state);
    return rows;
}

void validateLiveInstanceTopology(AcDbDatabase& source) {
    std::vector<AcDbObjectId> chain, active;
    InstanceWalk state{nullptr, {}};
    walk(acdbSymUtil()->blockModelSpaceId(&source), AcGeMatrix3d::kIdentity,
         chain, "", "0", active, state);
}

std::string instanceReceipt(const std::vector<Instance>& instances,
                            const std::vector<Unit>& units,
                            SemanticCatalogue& semantics) {
    // ObjectId -> cloned handle comes only from AutoCAD's IdMapping, never
    // from equal-looking coordinates, layer names or presumed stable handles.
    std::map<AcDbObjectId, std::pair<std::string, std::string>> handles;
    for (const auto& item : instances) {
        for (const auto id : item.chain) {
            if (handles.count(id)) continue;
            auto entity = open<AcDbEntity>(id);
            const auto unit = std::find_if(units.begin(), units.end(), [&](const auto& u) {
                return u.source == entity->database();
            });
            if (unit == units.end() || !unit->handles.count(id))
                throw std::runtime_error("native clone mapping absent: " + item.route);
            handles.emplace(id, std::make_pair(unit->handles.at(id), unit->file));
        }
    }
    std::ostringstream out;
    out << std::setprecision(17) << '[';
    bool first = true;
    for (std::size_t index = 0; index < instances.size(); ++index) {
        const auto& item = instances[index];
        std::string archiveRoute;
        for (const auto id : item.chain) {
            const auto found = handles.find(id);
            if (found == handles.end())
                throw std::runtime_error("native clone mapping absent: " + item.route);
            if (!archiveRoute.empty()) archiveRoute += '/';
            archiveRoute += found->second.first;
        }
        if (item.chain.empty()) throw std::runtime_error("capture instance chain absent");
        std::ostringstream row;
        row << std::setprecision(17) << "{\"source_route\":" << quote(item.route)
            << ",\"archive_route\":" << quote(archiveRoute)
            << ",\"archive_file\":" << quote(handles.at(item.chain.back()).second)
            << ",\"class\":" << quote(item.type)
            << ",\"authored_layer\":" << quote(item.authoredLayer)
            << ",\"effective_layer\":" << quote(item.effectiveLayer)
            << ",\"unavailable\":" << quote(item.unavailable) << ",\"transform\":[";
        for (int i = 0; i < 4; ++i) {
            row << (i ? ",[" : "[");
            for (int j = 0; j < 4; ++j) row << (j ? "," : "") << item.transform(i, j);
            row << ']';
        }
        row << "]}";
        const auto identity = row.str();
        out << (first ? "" : ",") << identity;
        first = false;
        semantics.consider(index, identity, item.semanticEligible, item.semantic.get());
    }
    out << ']';
    return out.str();
}
}
