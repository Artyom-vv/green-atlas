#include "session_capture_internal.h"
#include "dbsymutl.h"
#include "aced.h"
#include <algorithm>
#include <iomanip>
#include <sstream>

namespace ga::capture {
namespace {
void walk(AcDbObjectId recordId, const AcGeMatrix3d& transform,
          std::vector<AcDbObjectId>& chain, const std::string& route,
          std::vector<AcDbObjectId>& active, std::vector<Instance>& rows) {
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
        AcString layer;
        checked(entity->layer(layer), "capture layer");
        Instance item{address, AcString(entity->isA()->name()).utf8Str(),
                      layer.utf8Str(), {}, chain, transform};
        auto* reference = AcDbBlockReference::cast(entity.get());
        bool descend = reference != nullptr;
        if (reference) {
            auto child = open<AcDbBlockTableRecord>(reference->blockTableRecord());
            if (AcDbMInsertBlock::cast(reference)) item.unavailable = "array_instance";
            else if (child->isFromExternalReference() &&
                     (child->xrefStatus() != AcDb::kXrfResolved || !child->xrefDatabase()))
                item.unavailable = "xref_not_loaded";
            else if (std::find(active.begin(), active.end(), child->objectId()) != active.end())
                item.unavailable = "cyclic_block";
            else if (chain.size() >= 64) item.unavailable = "instance_depth_limit";
            descend = item.unavailable.empty();
        }
        rows.push_back(std::move(item));
        if (descend)
            walk(reference->blockTableRecord(), transform * reference->blockTransform(),
                 chain, address + "/", active, rows);
        chain.pop_back();
    }
    active.pop_back();
}
}

std::vector<Instance> captureInstances(AcDbDatabase& source) {
    std::vector<Instance> rows;
    std::vector<AcDbObjectId> chain, active;
    walk(acdbSymUtil()->blockModelSpaceId(&source), AcGeMatrix3d::kIdentity,
         chain, "", active, rows);
    return rows;
}

std::string instanceReceipt(const std::vector<Instance>& instances,
                            const std::vector<Unit>& units) {
    // ObjectId -> cloned handle comes only from AutoCAD's IdMapping, never
    // from equal-looking coordinates, layer names or presumed stable handles.
    std::map<AcDbObjectId, std::string> handles;
    for (const auto& item : instances) {
        for (const auto id : item.chain) {
            if (handles.count(id)) continue;
            auto entity = open<AcDbEntity>(id);
            const auto unit = std::find_if(units.begin(), units.end(), [&](const auto& u) {
                return u.source == entity->database();
            });
            if (unit == units.end() || !unit->handles.count(id))
                throw std::runtime_error("native clone mapping absent: " + item.route);
            handles.emplace(id, unit->handles.at(id));
        }
    }
    std::ostringstream out;
    out << std::setprecision(17) << '[';
    bool first = true;
    for (const auto& item : instances) {
        std::string archiveRoute;
        for (const auto id : item.chain) {
            const auto found = handles.find(id);
            if (found == handles.end())
                throw std::runtime_error("native clone mapping absent: " + item.route);
            if (!archiveRoute.empty()) archiveRoute += '/';
            archiveRoute += found->second;
        }
        out << (first ? "" : ",") << "{\"source_route\":" << quote(item.route)
            << ",\"archive_route\":" << quote(archiveRoute)
            << ",\"class\":" << quote(item.type) << ",\"layer\":" << quote(item.layer)
            << ",\"unavailable\":" << quote(item.unavailable) << ",\"transform\":[";
        first = false;
        for (int i = 0; i < 4; ++i) {
            out << (i ? ",[" : "[");
            for (int j = 0; j < 4; ++j) out << (j ? "," : "") << item.transform(i, j);
            out << ']';
        }
        out << "]}";
    }
    out << ']';
    return out.str();
}
}
