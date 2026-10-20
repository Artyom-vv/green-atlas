// SDK controls for the live producer preflight, in a private Core process.
// Creates only owned synthetic DWG; never saves an active/user drawing.
#include "rxregsvc.h"
#include "core_rxmfcapi.h"
#include "aced.h"
#include "adslib.h"
#include "dbapserv.h"
#include "dbents.h"
#include "dbsymtb.h"
#include "dbsymutl.h"
#include "acdbxref.h"
#include "DbField.h"
#include "AcString.h"
#include "../native/xref_dependency.h"
#include <cstdlib>
#include <dlfcn.h>
#include <filesystem>
#include <fstream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>

namespace {
namespace fs = std::filesystem;
using Hash = std::string (*)(const std::string&);
using Validate = void (*)(AcDbDatabase&);
using Dependency = ga::bridge::XrefDependency (*)(AcDbBlockTableRecord*);
constexpr unsigned kBoundaryBlocks = 63;
constexpr unsigned kOverflowBlocks = 64;

void checked(Acad::ErrorStatus status, const char* action) {
    if (status != Acad::eOk)
        throw std::runtime_error(std::string(action) + ": " + std::to_string(int(status)));
}
template<class T> struct Close { void operator()(T* item) const { if (item) item->close(); } };
template<class T> using Object = std::unique_ptr<T, Close<T>>;
template<class T> Object<T> open(AcDbObjectId id, AcDb::OpenMode mode = AcDb::kForRead) {
    T* item = nullptr;
    checked(acdbOpenObject(item, id, mode), "fixture object access");
    if (!item) throw std::runtime_error("fixture object absent");
    return Object<T>(item);
}
std::string quote(const std::string& text) {
    constexpr char hex[] = "0123456789abcdef";
    std::string result = "\"";
    for (unsigned char c : text) {
        if (c == '"' || c == '\\') { result += '\\'; result += char(c); }
        else if (c < 0x20) { result += "\\u00"; result += hex[c >> 4]; result += hex[c & 15]; }
        else result += char(c);
    }
    return result + '"';
}
void publish(const fs::path& path, const std::string& data) {
    if (fs::exists(path)) throw std::runtime_error("fresh control output required");
    std::ofstream output(path);
    output << data << '\n';
    if (!output) throw std::runtime_error("control output unavailable");
}
std::string handle(AcDbObjectId id) {
    ACHAR text[32] = {};
    id.handle().getIntoAsciiBuffer(text);
    return AcString(text).utf8Str();
}
std::string type(AcDbObjectId id) {
    return AcString(open<AcDbEntity>(id)->isA()->name()).utf8Str();
}
AcDbObjectId append(AcDbObjectId recordId, AcDbEntity* raw) {
    std::unique_ptr<AcDbEntity> pending(raw);
    auto record = open<AcDbBlockTableRecord>(recordId, AcDb::kForWrite);
    AcDbObjectId id;
    checked(record->appendAcDbEntity(id, pending.get()), "fixture append");
    pending.release()->close();
    return id;
}
AcDbObjectId oneEntity(AcDbObjectId recordId) {
    auto record = open<AcDbBlockTableRecord>(recordId);
    AcDbBlockTableRecordIterator* raw = nullptr;
    checked(record->newIterator(raw), "fixture iterator");
    std::unique_ptr<AcDbBlockTableRecordIterator> iterator(raw);
    if (iterator->done()) throw std::runtime_error("empty fixture record");
    AcDbObjectId id;
    checked(iterator->getEntityId(id), "fixture entity id");
    iterator->step();
    if (!iterator->done()) throw std::runtime_error("fixture record is not singular");
    return id;
}
void line(AcDbObjectId id) {
    auto value = open<AcDbLine>(id);
    if (value->startPoint() != AcGePoint3d(0,0,0) || value->endPoint() != AcGePoint3d(10,0,0))
        throw std::runtime_error("fixture line coordinates changed");
}
AcDbObjectId model(AcDbDatabase& db) { return acdbSymUtil()->blockModelSpaceId(&db); }
struct Working {
    AcDbHostApplicationServices* services = acdbHostApplicationServices();
    AcDbDatabase* previous = services->workingDatabase();
    explicit Working(AcDbDatabase& db) { services->setWorkingDatabase(&db); }
    ~Working() { services->setWorkingDatabase(previous); }
};
void saveNew(AcDbDatabase& db, const fs::path& path) {
    if (fs::exists(path)) throw std::runtime_error("existing fixture drawing refused");
    checked(db.saveAs(AcString(path.c_str()).kwszPtr(), false, AcDb::kDHL_CURRENT), "save new fixture");
}
void seedBlocks(const fs::path& file, unsigned blocks, bool array = false) {
    AcDbDatabase db(true, true);
    checked(db.setInsunits(AcDb::kUnitsMeters), "fixture units");
    Working context(db);
    std::vector<AcDbObjectId> records;
    auto table = open<AcDbBlockTable>(db.blockTableId(), AcDb::kForWrite);
    for (unsigned index = 0; index < blocks; ++index) {
        auto record = std::make_unique<AcDbBlockTableRecord>();
        const auto name = "GA_TOPOLOGY_" + std::to_string(index);
        checked(record->setName(AcString(name.c_str()).kwszPtr()), "fixture block name");
        AcDbObjectId id;
        checked(table->add(id, record.get()), "fixture block add");
        record.release()->close(); records.push_back(id);
    }
    table.reset();
    for (unsigned index = 0; index + 1 < blocks; ++index)
        append(records[index], new AcDbBlockReference({0,0,0}, records[index + 1]));
    append(records.back(), new AcDbLine({0,0,0}, {10,0,0}));
    if (array) append(model(db), new AcDbMInsertBlock({0,0,0}, records.front(), 3, 2, 20, 30));
    else append(model(db), new AcDbBlockReference({0,0,0}, records.front()));
    saveNew(db, file);
}
void seedXref(const fs::path& hostFile, const fs::path& childFile) {
    {
        AcDbDatabase child(true, true);
        checked(child.setInsunits(AcDb::kUnitsMeters), "child units");
        Working context(child);
        append(model(child), new AcDbLine({0,0,0}, {10,0,0}));
        saveNew(child, childFile);
    }
    AcDbDatabase host(true, true);
    checked(host.setInsunits(AcDb::kUnitsMeters), "host units");
    Working context(host);
    AcDbObjectId reference;
    checked(acdbAttachXref(&host, AcString(childFile.c_str()).kwszPtr(), L"GA_LOST_XREF", reference), "attach own XREF");
    append(model(host), new AcDbBlockReference({0,0,0}, reference));
    saveNew(host, hostFile);
}
void read(AcDbDatabase& db, const fs::path& file) {
    checked(db.readDwgFile(AcString(file.c_str()).kwszPtr(), AcDbDatabase::kForReadAndAllShare, true), "read fixture");
    checked(db.closeInput(true), "close fixture input");
}
struct Route {
    std::string address, leafClass;
    unsigned elements = 0;
};
Route chainOracle(AcDbDatabase& db, unsigned expectedBlocks) {
    Route result;
    auto recordId = model(db);
    for (unsigned index = 0; index <= expectedBlocks; ++index) {
        const auto id = oneEntity(recordId);
        if (!result.address.empty()) result.address += '/';
        result.address += handle(id); ++result.elements;
        if (index == expectedBlocks) {
            line(id); result.leafClass = type(id); return result;
        }
        auto reference = open<AcDbBlockReference>(id);
        if (AcDbMInsertBlock::cast(reference.get())) throw std::runtime_error("unexpected array in chain fixture");
        auto child = open<AcDbBlockTableRecord>(reference->blockTableRecord());
        if (child->isFromExternalReference()) throw std::runtime_error("unexpected XREF in chain fixture");
        recordId = child->objectId();
    }
    throw std::runtime_error("fixture chain did not terminate");
}
std::string validate(Validate operation, AcDbDatabase& db) {
    try { operation(db); return ""; }
    catch (const std::exception& failure) { return failure.what(); }
}
void refused(const std::string& error, const std::string& route,
             const std::string& entityClass, const std::string& reason) {
    const auto expected = "route=" + route + "; class=" + entityClass + "; reason=" + reason + ".";
    if (error.empty() || error.find(expected) == std::string::npos)
        throw std::runtime_error("topology refusal did not identify physical source: " + error);
}
void unchanged(Hash hash, const fs::path& file, const std::string& before) {
    if (before.empty() || hash(file.string()) != before)
        throw std::runtime_error("fixture source disk changed");
}
std::string blocksControl(const fs::path& file, unsigned blocks, bool array, Hash hash, Validate operation) {
    const auto before = hash(file.string());
    AcDbDatabase db(false, true); read(db, file); Working context(db);
    std::string route, entityClass, reason, oracle;
    if (array) {
        const auto id = oneEntity(model(db));
        auto value = open<AcDbMInsertBlock>(id);
        if (value->columns() != 3 || value->rows() != 2 ||
            value->columnSpacing() != 20 || value->rowSpacing() != 30)
            throw std::runtime_error("physical MINSERT fixture differs");
        line(oneEntity(value->blockTableRecord()));
        route = handle(id); entityClass = type(id); reason = "array_instance";
        oracle = "{\"columns\":3,\"rows\":2,\"physical_cells\":6,\"child_line\":[[0,0,0],[10,0,0]]}";
    } else {
        const auto physical = chainOracle(db, blocks);
        route = physical.address; entityClass = physical.leafClass;
        if (physical.elements != blocks + 1) throw std::runtime_error("native route element count differs");
        if (blocks == kOverflowBlocks) reason = "instance_depth_limit";
        oracle = "{\"block_references\":" + std::to_string(blocks) +
            ",\"route_elements\":" + std::to_string(physical.elements) + ",\"leaf_line\":[[0,0,0],[10,0,0]]}";
    }
    const auto error = validate(operation, db);
    if (reason.empty()) { if (!error.empty()) throw std::runtime_error("valid control refused: " + error); }
    else refused(error, route, entityClass, reason);
    if (array) {
        const auto id = oneEntity(model(db));
        auto value = open<AcDbMInsertBlock>(id);
        if (handle(id) != route || value->columns() != 3 || value->rows() != 2 ||
            value->columnSpacing() != 20 || value->rowSpacing() != 30)
            throw std::runtime_error("preflight changed physical MINSERT");
        line(oneEntity(value->blockTableRecord()));
    } else if (chainOracle(db, blocks).address != route)
        throw std::runtime_error("preflight changed physical route");
    unchanged(hash, file, before);
    return "{\"source\":" + quote(file.filename().string()) + ",\"passed\":true,\"source_sha256\":" + quote(before) +
        ",\"source_disk_unchanged\":true,\"route\":" + quote(route) + ",\"class\":" + quote(entityClass) +
        ",\"expected_reason\":" + quote(reason) + ",\"oracle\":" + oracle + ",\"error\":" + quote(error) + "}";
}
std::string xrefControl(const fs::path& hostFile, const fs::path& childFile, const fs::path& hidden,
                       Hash hash, Validate operation, Dependency dependency) {
    const auto hostBefore = hash(hostFile.string()), childBefore = hash(childFile.string());
    AcDbDatabase db(false, true); read(db, hostFile); Working context(db);
    checked(acdbResolveCurrentXRefs(&db, false, false), "resolve own XREF");
    const auto refId = oneEntity(model(db));
    auto reference = open<AcDbBlockReference>(refId);
    auto record = open<AcDbBlockTableRecord>(reference->blockTableRecord());
    auto* loaded = record->xrefDatabase();
    if (!record->isFromExternalReference() || record->xrefStatus() != AcDb::kXrfResolved || !loaded)
        throw std::runtime_error("XREF control is not actually resolved and loaded");
    line(oneEntity(record->objectId()));
    checked(loaded->closeInput(true), "close loaded XREF input");
    const auto provenanceBefore = dependency(record.get());
    if (provenanceBefore.status != "resolved" || !validate(operation, db).empty())
        throw std::runtime_error("available loaded XREF baseline did not pass");
    if (fs::exists(hidden)) throw std::runtime_error("fresh hidden fixture path required");
    fs::rename(childFile, hidden);
    struct Restore {
        fs::path source, hidden;
        ~Restore() { std::error_code error; fs::rename(hidden, source, error); }
    } restore{childFile, hidden};
    if (fs::exists(childFile) || record->xrefStatus() != AcDb::kXrfResolved || record->xrefDatabase() != loaded)
        throw std::runtime_error("missing-disk control lost the loaded native database");
    line(oneEntity(record->objectId()));
    const auto provenanceAfter = dependency(record.get());
    if (provenanceAfter.status == "resolved") throw std::runtime_error("missing XREF retained disk provenance");
    const auto error = validate(operation, db);
    refused(error, handle(refId), type(refId), "loaded_xref_disk_provenance_unavailable");
    if (record->xrefStatus() != AcDb::kXrfResolved || record->xrefDatabase() != loaded)
        throw std::runtime_error("preflight replaced loaded XREF database");
    line(oneEntity(record->objectId())); unchanged(hash, hostFile, hostBefore); unchanged(hash, hidden, childBefore);
    return "{\"source\":" + quote(hostFile.filename().string()) + ",\"passed\":true,\"baseline_passed\":true,"
        "\"native_xref_status\":" + std::to_string(int(record->xrefStatus())) +
        ",\"native_resolved_and_loaded\":true,\"native_database_retained\":true,\"backing_path_absent_during_test\":true,"
        "\"disk_provenance_before\":" + quote(provenanceBefore.status) + ",\"disk_provenance_after\":" + quote(provenanceAfter.status) +
        ",\"host_disk_unchanged\":true,\"hidden_child_disk_unchanged\":true,\"host_sha256\":" + quote(hostBefore) +
        ",\"child_sha256\":" + quote(childBefore) + ",\"route\":" + quote(handle(refId)) +
        ",\"class\":" + quote(type(refId)) + ",\"expected_reason\":\"loaded_xref_disk_provenance_unavailable\",\"error\":" + quote(error) + "}";
}
template<class T> T symbol(void* library, const char* name) {
    auto pointer = reinterpret_cast<T>(dlsym(library, name));
    if (!pointer) throw std::runtime_error(std::string("private topology symbol unavailable: ") + name);
    return pointer;
}
void controls() {
    const auto* rootText = std::getenv("GA_CORE_INTAKE_ROOT");
    const auto* libraryText = std::getenv("GA_PRODUCTION_CAPTURE_LIBRARY");
    const auto* action = std::getenv("GA_CORE_INTAKE_ACTION");
    if (!rootText || !libraryText || !action || std::string(action) != "topology") return;
    fs::path root;
    try {
        const auto requestedRoot = fs::canonical(rootText);
        const auto repo = fs::canonical(fs::path(__FILE__).parent_path().parent_path().parent_path().parent_path());
        const auto relative = requestedRoot.lexically_relative(repo / "output");
        const auto libraryFile = fs::canonical(libraryText);
        const auto libraryRelative = libraryFile.lexically_relative(repo / ".runtime/autocad-bridge/macos");
        struct stat info = {};
        if (relative.empty() || relative.is_absolute() || *relative.begin() == ".." ||
            requestedRoot.filename().string().rfind("live-topology-controls-", 0) != 0 ||
            libraryRelative.empty() || libraryRelative.is_absolute() || *libraryRelative.begin() == ".." ||
            lstat(requestedRoot.c_str(), &info) != 0 || !S_ISDIR(info.st_mode) || info.st_uid != getuid() || (info.st_mode & 0077))
            throw std::runtime_error("owned private topology output/build required");
        root = requestedRoot;
        void* library = dlopen(libraryFile.c_str(), RTLD_NOW | RTLD_LOCAL);
        if (!library) throw std::runtime_error(std::string("private topology library: ") + dlerror());
        struct Library { void* value; ~Library() { dlclose(value); } } loaded{library};
        const auto hash = symbol<Hash>(library, "_ZN2ga6bridge10sha256FileERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE");
        const auto operation = symbol<Validate>(library, "_ZN2ga7capture28validateLiveInstanceTopologyER12AcDbDatabase");
        const auto dependency = symbol<Dependency>(library, "_ZN2ga6bridge21inspectXrefDependencyEP20AcDbBlockTableRecord");
        const auto source = root / "source", hidden = root / "hidden";
        if (!fs::create_directory(source) || !fs::create_directory(hidden))
            throw std::runtime_error("fresh synthetic topology directories required");
        fs::permissions(source, fs::perms::owner_all, fs::perm_options::replace);
        fs::permissions(hidden, fs::perms::owner_all, fs::perm_options::replace);
        auto* previous = acdbHostApplicationServices()->workingDatabase();
        struct Fields {
            AcDbField::EvalOption previous = acdbGlobalFieldEvaluationOption();
            Fields() { checked(acdbSetGlobalFieldEvaluationOption(AcDbField::kDisable), "disable fixture field evaluation"); }
            ~Fields() { acdbSetGlobalFieldEvaluationOption(previous); }
        } fields;
        seedBlocks(source / "ordinary.dwg", 1); seedBlocks(source / "array.dwg", 1, true);
        seedBlocks(source / "leaf64.dwg", kBoundaryBlocks); seedBlocks(source / "leaf65.dwg", kOverflowBlocks);
        seedXref(source / "xref-host.dwg", source / "xref-child.dwg");
        const auto ordinary = blocksControl(source / "ordinary.dwg", 1, false, hash, operation);
        const auto array = blocksControl(source / "array.dwg", 1, true, hash, operation);
        const auto boundary = blocksControl(source / "leaf64.dwg", kBoundaryBlocks, false, hash, operation);
        const auto overflow = blocksControl(source / "leaf65.dwg", kOverflowBlocks, false, hash, operation);
        const auto xref = xrefControl(source / "xref-host.dwg", source / "xref-child.dwg", hidden / "xref-child.held", hash, operation, dependency);
        if (!fs::is_regular_file(source / "xref-child.dwg") || fs::exists(hidden / "xref-child.held"))
            throw std::runtime_error("owned XREF backing file was not restored");
        checked(acdbSetGlobalFieldEvaluationOption(fields.previous), "restore fixture field evaluation");
        if (acdbHostApplicationServices()->workingDatabase() != previous || acdbGlobalFieldEvaluationOption() != fields.previous)
            throw std::runtime_error("topology lifecycle not restored");
        publish(root / "topology-controls.json", "{\"schema\":\"green-atlas.live-topology-controls/1\",\"passed\":true,\"library_sha256\":" +
            quote(hash(libraryFile.string())) + ",\"controls\":[" + ordinary + "," + array + "," + boundary + "," + overflow + "," + xref +
            "],\"xref_backing_file_restored\":true,\"active_document_never_saved\":true}");
        publish(root / "topology-lifecycle.json", "{\"database_destroyed\":true,\"working_database_restored\":true,\"field_evaluation_restored\":true}");
        publish(root / "topology-native-completed.json", "{\"completed\":true}");
    } catch (const std::exception& failure) {
        if (!root.empty()) publish(root / "topology-native-error.json", "{\"error\":" + quote(failure.what()) + "}");
        acutPrintf(L"\nPRIVATE_TOPOLOGY_ERROR: %s", AcString(failure.what()).kwszPtr());
    }
}
}
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode message, void* id) {
    if (message == AcRx::kInitAppMsg) {
        acrxDynamicLinker->unlockApplication(id); acrxDynamicLinker->registerAppMDIAware(id);
        // Reuse the existing private Core run_stage lifecycle/timeout harness.
        acedRegCmds->addCommand(L"GA_PRIVATE_LIVE_TOPOLOGY", L"GAINTAKEFILE", L"GAINTAKEFILE", ACRX_CMD_MODAL, controls);
    } else if (message == AcRx::kUnloadAppMsg) acedRegCmds->removeGroup(L"GA_PRIVATE_LIVE_TOPOLOGY");
    return AcRx::kRetOK;
}
