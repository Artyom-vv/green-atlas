#include "session_capture.h"
#include "session_capture_internal.h"
#include "bridge_config.h"
#include "dbidmap.h"
#include "acdbxref.h"
#include "dbapserv.h"
#include "rxevent.h"
#include "aced.h"
#include "adslib.h"
#include <set>
#include <sstream>

namespace ga::capture {
namespace {
int dbmod() {
    resbuf value{};
    if (acedGetVar(L"DBMOD", &value) != RTNORM || value.restype != RTSHORT)
        throw std::runtime_error("capture DBMOD unavailable");
    return value.resval.rint;
}
struct Symbols {
    AcDbDatabase* db;
    bool active = false;
    explicit Symbols(AcDbDatabase* value) : db(value) {}
    void begin() {
        checked(db->restoreOriginalXrefSymbols(), "capture restore XREF symbols");
        active = true;
    }
    void end() {
        if (!active) return;
        checked(db->restoreForwardingXrefSymbols(), "capture restore XREF forwarding");
        active = false;
    }
    ~Symbols() { if (active) db->restoreForwardingXrefSymbols(); }
};
struct CloneMap : AcRxEventReactor {
    AcDbDatabase* source;
    std::map<AcDbDatabase*, std::map<AcDbObjectId, std::string>> maps;
    bool invalid = false;
    explicit CloneMap(AcDbDatabase* db) : source(db) { acrxEvent->addReactor(this); }
    ~CloneMap() override { acrxEvent->removeReactor(this); }
    void wblockNotice(AcDbDatabase* db) override {
        if (db == source) db->forceWblockDatabaseCopy();
    }
    void endDeepClone(AcDbIdMapping& mapping) override {
        // Never throw through Autodesk's callback stack.
        try {
            AcDbDatabase *origin = nullptr, *destination = nullptr;
            mapping.origDb(origin); mapping.destDb(destination);
            // Whole WBLOCK can clone dependency records in nested callbacks
            // whose origDb is an XREF, not this host. Only the destination
            // identifies which native map belongs to the private copy.
            if (!destination || destination == source) return;
            AcDbIdMappingIter it(mapping);
            for (it.start(); !it.done(); it.next()) {
                AcDbIdPair pair;
                if (!it.getMap(pair)) { invalid = true; continue; }
                if (pair.value().isNull()) continue;
                auto& entries = maps[destination];
                const auto target = handle(pair.value());
                auto result = entries.emplace(pair.key(), target);
                if (!result.second && result.first->second != target) invalid = true;
                auto redirected = pair.key();
                redirected.convertToRedirectedId();
                result = entries.emplace(redirected, target);
                if (!result.second && result.first->second != target) invalid = true;
            }
        } catch (...) { invalid = true; }
    }
};
std::vector<Link> links(AcDbDatabase& source,
                       const std::map<std::string, AcDbDatabase*>* loaded = nullptr) {
    auto table = open<AcDbBlockTable>(source.blockTableId());
    AcDbBlockTableIterator* raw = nullptr;
    checked(table->newIterator(raw), "capture XREF iterator");
    std::unique_ptr<AcDbBlockTableIterator> it(raw);
    std::vector<Link> result;
    for (; !it->done(); it->step()) {
        AcDbObjectId id;
        checked(it->getRecordId(id), "capture XREF id");
        auto record = open<AcDbBlockTableRecord>(id);
        if (!record->isFromExternalReference()) continue;
        AcString name, path;
        checked(record->getName(name), "capture XREF name");
        record->pathName(path);
        auto* target = record->xrefStatus() == AcDb::kXrfResolved ? record->xrefDatabase() : nullptr;
        int status = int(record->xrefStatus());
        if (loaded && !record->isUnloaded()) {
            // Restoring the owning XREF's symbols yields its ORIGINAL ids
            // (required by IdMapping), but temporarily loses nested status.
            // Resolve the authored path with AutoCAD in that owner's context,
            // then identify it in the pre-captured loaded graph. No basename
            // matching, file reread, or substitution for the loaded database.
            AcString resolved;
            if (acdbHostApplicationServices()->findFile(resolved, path.kwszPtr(), &source,
                    AcDbHostApplicationServices::kXRefDrawing) == Acad::eOk) {
                const auto found = loaded->find(fs::weakly_canonical(resolved.utf8Str()).string());
                if (found != loaded->end()) { target = found->second; status = int(AcDb::kXrfResolved); }
            }
        }
        result.push_back({id, target, name.utf8Str(), path.utf8Str(),
                          status, bool(record->isUnloaded())});
    }
    return result;
}
std::string filename(AcDbDatabase& database) {
    const ACHAR* raw = nullptr;
    checked(database.getFilename(raw), "capture source filename");
    return raw ? AcString(raw).utf8Str() : "";
}
void clone(Unit& unit, bool isHost, const std::set<AcDbDatabase*>& sources,
           const std::map<std::string, AcDbDatabase*>& loaded) {
    if (acedUsrBrk()) throw std::runtime_error("Захват отменён");
    Symbols symbols(unit.source);
    if (!isHost) symbols.begin();
    unit.links = links(*unit.source, &loaded);
    CloneMap map(unit.source);
    AcDbDatabase* raw = nullptr;
    const auto status = unit.source->wblock(raw);
    // Guard before ownership: the source must never be deleted even if AutoCAD
    // unexpectedly returns it instead of a forced copy.
    if (raw && sources.count(raw)) throw std::runtime_error("capture returned live database");
    unit.copy.reset(raw);
    symbols.end();
    checked(status, "capture WBLOCK copy");
    if (!unit.copy || map.invalid || !map.maps.count(raw))
        throw std::runtime_error("native clone identity incomplete");
    unit.handles = std::move(map.maps.at(raw));
}
void localize(Unit& unit, const std::map<AcDbDatabase*, std::string>& files,
              std::ostream& receipt, bool& firstLink) {
    // WBLOCK does not copy a host's runtime-only nested XREF records. Their
    // owners live in the child DWGs. Require an exact native mapping for EVERY
    // reference actually present in each copy, not a phantom host edge.
    for (const auto& copied : links(*unit.copy)) {
        const auto copiedHandle = handle(copied.sourceId);
        const Link* original = nullptr;
        for (const auto& candidate : unit.links) {
            const auto mapped = unit.handles.find(candidate.sourceId);
            if (mapped == unit.handles.end() || mapped->second != copiedHandle) continue;
            if (original) throw std::runtime_error("ambiguous cloned XREF owner");
            original = &candidate;
        }
        if (!original) throw std::runtime_error("native XREF record mapping absent: "
            + unit.file + "/" + copiedHandle + " " + copied.name);
        const auto& link = *original;
        auto record = open<AcDbBlockTableRecord>(copied.sourceId, AcDb::kForWrite);
        const auto target = files.find(link.target);
        if (link.target && target == files.end())
            throw std::runtime_error("loaded XREF not present in native graph");
        // Missing/unloaded source refs must not resolve from another directory
        // or AutoCAD support path when the private package is reopened.
        const auto path = target != files.end() ? target->second
            : "not-captured-" + unit.file + "-" + copiedHandle + ".dwg";
        checked(record->setPathName(AcString(("./" + path).c_str()).kwszPtr()),
                "capture private XREF path");
        receipt << (firstLink ? "" : ",") << "{\"owner\":" << quote(unit.file)
            << ",\"archive_record\":" << quote(copiedHandle)
            << ",\"name\":" << quote(link.name) << ",\"authored\":" << quote(link.authored)
            << ",\"source_status\":" << link.status
            << ",\"source_unloaded\":" << (link.unloaded ? "true" : "false")
            << ",\"available\":" << (target != files.end() ? "true" : "false")
            << ",\"archive_path\":" << quote(path) << '}';
        firstLink = false;
    }
}
}

void capturePackage(AcDbDatabase& source, const std::string& destination,
                    const std::string& displaySha256) {
    const fs::path root(destination);
    if (!root.is_absolute() || fs::exists(root) || !fs::is_directory(root.parent_path()))
        throw std::runtime_error("fresh private capture directory required");
    fs::create_directory(root);
    fs::permissions(root, fs::perms::owner_all, fs::perm_options::replace);
    const int beforeFlags = dbmod();
    const auto sourceName = filename(source);
    const auto instances = captureInstances(source);
    AcDbXrefGraph graph;
    checked(acdbGetHostDwgXrefGraph(&source, graph, true), "capture loaded XREF graph");
    std::vector<Unit> units;
    std::set<AcDbDatabase*> sources{&source};
    std::map<AcDbDatabase*, std::string> files{{&source, "host.dwg"}};
    units.push_back({&source, "host.dwg", {}, {}, {}});
    for (int i = 1; i < graph.numNodes(); ++i) {
        auto* node = graph.xrefNode(i);
        if (node->xrefStatus() != AcDb::kXrfResolved || !node->database()) continue;
        if (!sources.insert(node->database()).second) continue;
        const auto file = "xref-" + std::to_string(units.size()) + ".dwg";
        files.emplace(node->database(), file);
        units.push_back({node->database(), file, {}, {}, {}});
    }
    std::map<std::string, std::string> diskHashes;
    std::map<std::string, AcDbDatabase*> loaded;
    for (const auto& unit : units) {
        const auto name = filename(*unit.source);
        if (!name.empty()) {
            const auto added = loaded.emplace(fs::weakly_canonical(name).string(), unit.source);
            if (!added.second && added.first->second != unit.source)
                throw std::runtime_error("ambiguous loaded native database path");
        }
        if (!name.empty() && fs::is_regular_file(name)) {
            const auto hash = ga::bridge::sha256File(name);
            if (hash.empty()) throw std::runtime_error("capture source hash unavailable");
            diskHashes.emplace(name, hash);
        }
    }
    for (auto& unit : units) clone(unit, unit.source == &source, sources, loaded);
    const auto identity = instanceReceipt(instances, units);
    std::ostringstream refs, catalogue;
    refs << '['; catalogue << '[';
    bool firstLink = true, firstFile = true;
    for (auto& unit : units) {
        if (acedUsrBrk()) throw std::runtime_error("Захват отменён");
        localize(unit, files, refs, firstLink);
        const auto path = root / unit.file;
        checked(unit.copy->saveAs(AcString(path.string().c_str()).kwszPtr(), false,
                                  AcDb::kDHL_CURRENT), "save private capture");
        const auto hash = ga::bridge::sha256File(path.string());
        if (hash.empty()) throw std::runtime_error("capture output hash unavailable");
        catalogue << (firstFile ? "" : ",") << "{\"path\":" << quote(unit.file)
            << ",\"sha256\":" << quote(hash) << ",\"bytes\":" << fs::file_size(path) << '}';
        firstFile = false;
    }
    refs << ']'; catalogue << ']';
    units.clear(); // Source guards cover destruction of all native copies too.
    if (filename(source) != sourceName) throw std::runtime_error("capture changed source filename");
    for (const auto& item : diskHashes)
        if (ga::bridge::sha256File(item.first) != item.second)
            throw std::runtime_error("source file changed during capture");
    publish(root / "instances.json", identity);
    std::ostringstream receipt;
    receipt << "{\"schema\":\"green-atlas.native-session/1\",\"plugin_version\":"
        << quote(ga::bridge::kPluginVersion) << ",\"entry\":\"host.dwg\",\"units_code\":"
        << int(source.insunits()) << ",\"source_path\":" << quote(sourceName)
        << ",\"display_sha256\":" << quote(displaySha256)
        << ",\"source_dbmod_before\":" << beforeFlags << ",\"source_dbmod_after\":" << dbmod()
        << ",\"source_disk_hashes_verified\":true,\"instance_count\":" << instances.size()
        << ",\"instances_sha256\":" << quote(ga::bridge::sha256File((root / "instances.json").string()))
        << ",\"files\":" << catalogue.str() << ",\"references\":" << refs.str() << '}';
    publish(root / "session.json", receipt.str());
}
}
