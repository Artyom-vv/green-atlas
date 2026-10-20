// Explicit GUI-only qualification of a built, uninstalled live session.
// Reads one owned source copy. An explicit control mode can seed/edit a tiny
// synthetic drawing; it never saves an active document or resets DBMOD.
#include "rxregsvc.h"
#include "core_rxmfcapi.h"
#include "aced.h"
#include "adslib.h"
#include "dbapserv.h"
#include "acdocman.h"
#include "AcString.h"
#include "dbents.h"
#include "dbsymtb.h"
#include "dbsymutl.h"
#include "live_delivery_probe.h"
#include "../native/document_lock_policy.h"
#include <cstdlib>
#include <dlfcn.h>
#include <filesystem>
#include <fstream>
#include <memory>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

namespace {
namespace fs = std::filesystem;
using Open = std::string (*)(const std::string&, const std::string&);
using Inspect = std::string (*)(const std::string&);
using Hash = std::string (*)(const std::string&);
using Close = void (*)();
constexpr const char* kControlSchema = "green-atlas.live-control-fixture/1";

void require(Acad::ErrorStatus status, const char* action) {
    if (status != Acad::eOk)
        throw std::runtime_error(std::string(action) + ": " + std::to_string(int(status)));
}
template<class T> struct CloseObject { void operator()(T* value) const { if(value) value->close(); } };
template<class T> using Object = std::unique_ptr<T, CloseObject<T>>;
template<class T> Object<T> opened(const AcDbObjectId& id, AcDb::OpenMode mode = AcDb::kForRead) {
    T* value = nullptr;
    require(acdbOpenObject(value, id, mode), "control object unavailable");
    if (!value) throw std::runtime_error("null control object");
    return Object<T>(value);
}

std::string appended(AcDbDatabase& database, AcDbEntity* entity) {
    std::unique_ptr<AcDbEntity> pending(entity);
    auto model = opened<AcDbBlockTableRecord>(acdbSymUtil()->blockModelSpaceId(&database), AcDb::kForWrite);
    AcDbObjectId id;
    require(model->appendAcDbEntity(id, pending.get()), "control append failed");
    auto* owned = pending.release();
    AcDbHandle handle;
    ACHAR text[32] = {};
    owned->getAcDbHandle(handle); handle.getIntoAsciiBuffer(text);
    owned->close();
    return AcString(text).utf8Str();
}

void seedControl(const fs::path& root, const fs::path& source, Hash hash) {
    const auto marker = root / "synthetic-fixture.txt";
    if (source != root / "input/host-control.dwg" || fs::exists(source) || fs::exists(marker))
        throw std::runtime_error("fresh synthetic control source required");
    auto* services = acdbHostApplicationServices();
    auto* previous = services->workingDatabase();
    {
        AcDbDatabase database(true, true);
        require(database.setInsunits(AcDb::kUnitsMeters), "control units");
        appended(database, new AcDbLine({0, 0, 0}, {10, 0, 0}));
        require(database.saveAs(AcString(source.c_str()).kwszPtr(), false, AcDb::kDHL_CURRENT),
                "save new synthetic fixture");
    }
    if (services->workingDatabase() != previous)
        throw std::runtime_error("synthetic seed changed working database");
    const auto digest = hash(source.string());
    if (digest.size() != 64) throw std::runtime_error("synthetic seed hash unavailable");
    std::ofstream out(marker);
    out << kControlSchema << '\n' << digest << '\n';
    if (!out) throw std::runtime_error("synthetic seed marker unavailable");
    acutPrintf(L"\nLIVE_CONTROL_SEEDED");
}

std::string controlDigest(const fs::path& root, const fs::path& source, Hash hash) {
    if (source != root / "input/host-control.dwg")
        throw std::runtime_error("control requires the native-seeded fixture");
    const auto marker = root / "synthetic-fixture.txt";
    if (!fs::is_regular_file(marker) || fs::file_size(marker) > 256)
        throw std::runtime_error("synthetic fixture marker unavailable");
    std::ifstream input(marker);
    std::string schema, digest, extra;
    if (!std::getline(input, schema) || !std::getline(input, digest) || std::getline(input, extra) ||
        schema != kControlSchema || digest.size() != 64 || hash(source.string()) != digest)
        throw std::runtime_error("synthetic fixture provenance changed");
    return digest;
}

void verifyControl(AcDbDatabase& database, bool withPoint, bool movedLine = false) {
    if (database.insunits() != AcDb::kUnitsMeters)
        throw std::runtime_error("synthetic units changed");
    auto table = opened<AcDbBlockTable>(database.blockTableId());
    AcDbBlockTableIterator* blockRaw = nullptr;
    require(table->newIterator(blockRaw), "control block iterator");
    std::unique_ptr<AcDbBlockTableIterator> blocks(blockRaw);
    for (; !blocks->done(); blocks->step()) {
        AcDbObjectId id;
        require(blocks->getRecordId(id), "control block id");
        auto record = opened<AcDbBlockTableRecord>(id);
        if (record->isFromExternalReference()) throw std::runtime_error("synthetic control cannot contain XREF");
    }
    auto model = opened<AcDbBlockTableRecord>(acdbSymUtil()->blockModelSpaceId(&database));
    AcDbBlockTableRecordIterator* entityRaw = nullptr;
    require(model->newIterator(entityRaw), "control entity iterator");
    std::unique_ptr<AcDbBlockTableRecordIterator> entities(entityRaw);
    unsigned lines = 0, points = 0;
    for (; !entities->done(); entities->step()) {
        AcDbEntity* raw = nullptr;
        require(entities->getEntity(raw, AcDb::kForRead), "control entity");
        Object<AcDbEntity> entity(raw);
        if (auto* line = AcDbLine::cast(raw)) {
            if (line->startPoint() != AcGePoint3d(0, 0, 0) ||
                line->endPoint() != AcGePoint3d(movedLine ? 12 : 10, 0, 0))
                throw std::runtime_error("synthetic source line changed");
            ++lines;
        } else if (auto* point = AcDbPoint::cast(raw)) {
            if (point->position() != AcGePoint3d(17, 23, 0))
                throw std::runtime_error("synthetic control point changed");
            ++points;
        } else throw std::runtime_error("synthetic source has unexpected entity");
    }
    if (lines != 1 || points != unsigned(withPoint))
        throw std::runtime_error("synthetic fixture contents changed");
}

std::string movedControlLine(AcDbDatabase& database) {
    auto model = opened<AcDbBlockTableRecord>(acdbSymUtil()->blockModelSpaceId(&database));
    AcDbBlockTableRecordIterator* raw = nullptr;
    require(model->newIterator(raw), "control line iterator");
    std::unique_ptr<AcDbBlockTableRecordIterator> iterator(raw);
    AcDbObjectId id;
    require(iterator->getEntityId(id), "control line id");
    auto line = opened<AcDbLine>(id, AcDb::kForWrite);
    AcDbHandle handle;
    ACHAR text[32] = {};
    line->getAcDbHandle(handle); handle.getIntoAsciiBuffer(text);
    require(line->setEndPoint({12, 0, 0}), "control coordinate modification failed");
    return AcString(text).utf8Str();
}

int modificationFlags() {
    resbuf value = {};
    if (acedGetVar(L"DBMOD", &value) != RTNORM || value.restype != RTSHORT)
        throw std::runtime_error("control DBMOD unavailable");
    return value.resval.rint;
}

void editControl(AcApDocument* document, AcDbDatabase& database, const fs::path& root,
                 const fs::path& source, const std::string& token, const std::string& mode,
                 Inspect inspect, Hash hash) {
    const auto digest = controlDigest(root, source, hash);
    verifyControl(database, false);
    const auto baseline = inspect(token); // A failing baseline cannot pass the control.
    const auto beforeFlags = modificationFlags();
    const bool point = mode == "append-point-control";
    const auto handle = point ? appended(database, new AcDbPoint({17, 23, 0})) : movedControlLine(database);
    verifyControl(database, point, !point);
    const auto afterFlags = modificationFlags();
    if (acDocManager->curDocument() != document || document->database() != &database ||
        acdbHostApplicationServices()->workingDatabase() != &database)
        throw std::runtime_error("control document context changed");
    if (hash(source.string()) != digest) throw std::runtime_error("control saved source unexpectedly");
    std::string rejection;
    try { inspect(token); }
    catch (const std::exception& error) { rejection = error.what(); }
    if (rejection != "live_source_changed_or_inactive")
        throw std::runtime_error("true source edit was not rejected by the watcher: " + rejection);
    std::ofstream out(root / "control-receipt.json");
    out << "{\"schema\":\"green-atlas.live-edit-control/1\",\"passed\":true,"
        << "\"mode\":\"" << mode << "\",\"baseline_inspect_ok\":true,\"changed_class\":\""
        << (point ? "AcDbPoint" : "AcDbLine") << "\",\"changed_handle\":\"" << handle
        << "\",\"physical_change\":"
        << (point ? "{\"appended_position\":[17,23,0]}" : "{\"endpoint_before\":[10,0,0],\"endpoint_after\":[12,0,0]}")
        << ",\"model_entities_before\":1,\"model_entities_after\":" << (point ? 2 : 1)
        << ",\"dbmod_before\":" << beforeFlags << ",\"dbmod_after\":" << afterFlags << ','
        << "\"document_context_unchanged\":true,\"source_disk_sha256\":\"" << digest
        << "\",\"source_disk_unchanged\":true,\"error\":\"" << rejection
        << "\",\"baseline_session\":" << baseline << "}\n";
    if (!out) throw std::runtime_error("control receipt unavailable");
    // The change remains a real unsaved edit. Close the owned document with
    // discard; never erase it or reset DBMOD to make this test look passive.
    acutPrintf(L"\nLIVE_CONTROL_PASSED: discard owned synthetic drawing on close.");
}

class LockChanges final : public AcApDocManagerReactor {
    AcApDocument* document;
public:
    unsigned count = 0;
    explicit LockChanges(AcApDocument* source) : document(source) { acDocManager->addReactor(this); }
    ~LockChanges() override { acDocManager->removeReactor(this); }
    void documentLockModeWillChange(AcApDocument* source, AcAp::DocLockMode,
                                   AcAp::DocLockMode, AcAp::DocLockMode, const ACHAR*) override {
        if (source == document) ++count;
    }
};

void archiveWriteControl(AcApDocument* document, AcDbDatabase& database, const fs::path& root,
                         const fs::path& source, const std::string& token,
                         Inspect inspect, Hash hash, Open archive) {
    const auto digest = controlDigest(root, source, hash);
    verifyControl(database, false);
    inspect(token);
    const auto before = document->myLockMode();
    if (!ga::liveQuery::canBorrowDocumentWrite(before))
        throw std::runtime_error("modal fixture does not own a write lock");
    const auto flagsBefore = modificationFlags();
    LockChanges events(document);
    const auto identity = archive(token, (root / "Native").string());
    const auto after = document->myLockMode();
    if (identity.empty() || !fs::is_regular_file(root / "Native/session.json") ||
        events.count != 0 || after != before)
        throw std::runtime_error("archive changed the command's native lock ownership");
    verifyControl(database, false);
    if (acDocManager->curDocument() != document || document->database() != &database ||
        acdbHostApplicationServices()->workingDatabase() != &database || hash(source.string()) != digest)
        throw std::runtime_error("write ownership control changed source/context");
    std::ofstream out(root / "write-ownership-receipt.json");
    out << "{\"schema\":\"green-atlas.document-write-ownership-control/1\",\"passed\":true,"
        << "\"my_lock_before\":" << int(before) << ",\"my_lock_after\":" << int(after)
        << ",\"source_document_lock_changes\":" << events.count
        << ",\"native_archive_completed\":true,\"physical_line_unchanged\":true,"
        << "\"source_disk_unchanged\":true,\"source_sha256\":\"" << digest
        << "\",\"dbmod_before\":" << flagsBefore << ",\"dbmod_after\":" << modificationFlags() << "}\n";
    if (!out) throw std::runtime_error("write ownership receipt unavailable");
    // Cloning notifications remain real invalidations. Do not inspect/reset
    // the session after archive just to manufacture a valid post-clone token.
    acutPrintf(L"\nLIVE_WRITE_OWNERSHIP_PASSED");
}

bool inside(const fs::path& path, const fs::path& root) {
    const auto relative = path.lexically_relative(root);
    return !relative.empty() && !relative.is_absolute() && *relative.begin() != "..";
}

struct DiagnosticEnvironment {
    bool present = false;
    std::string previous;
    DiagnosticEnvironment() {
        const char* value = std::getenv("GA_LIVE_CAPTURE_DIAGNOSTICS");
        present = value != nullptr;
        if (value) previous = value;
        if (setenv("GA_LIVE_CAPTURE_DIAGNOSTICS", "1", 1) != 0)
            throw std::runtime_error("cannot enable private live diagnostics");
    }
    ~DiagnosticEnvironment() {
        if (present) setenv("GA_LIVE_CAPTURE_DIAGNOSTICS", previous.c_str(), 1);
        else unsetenv("GA_LIVE_CAPTURE_DIAGNOSTICS");
    }
};

void run() {
    ACHAR request[4096] = {};
    if (acedGetString(1, L"\nOwned live capture request: ", request) != RTNORM) return;
    fs::path root;
    try {
        std::ifstream input(AcString(request).utf8Str());
        std::string libraryText, rootText, sourceText, token, mode = "capture", extra;
        if (!std::getline(input, libraryText) || !std::getline(input, rootText) ||
            !std::getline(input, sourceText) || !std::getline(input, token))
            throw std::runtime_error("invalid private capture request");
        if (std::getline(input, extra)) {
            mode = extra;
            if (std::getline(input, extra)) throw std::runtime_error("unexpected private request line");
        }
        if (mode != "capture" && mode != "seed-control" && mode != "append-point-control" &&
            mode != "move-line-control" && mode != "delivery-parity" && mode != "archive-write-control")
            throw std::runtime_error("unknown private capture mode");
        const auto repo = fs::canonical(fs::path(__FILE__).parent_path().parent_path().parent_path().parent_path());
        const auto requestedRoot = fs::canonical(rootText);
        const auto source = mode == "seed-control" ? fs::weakly_canonical(sourceText) : fs::canonical(sourceText);
        const auto libraryPath = fs::canonical(libraryText);
        struct stat info = {};
        if (!inside(requestedRoot, repo / "output") || !inside(source, requestedRoot / "input") ||
            !inside(libraryPath, repo / ".runtime/autocad-bridge/macos"))
            throw std::runtime_error("own private capture source/build/output required");
        if (lstat(requestedRoot.c_str(), &info) != 0 || !S_ISDIR(info.st_mode) ||
            info.st_uid != getuid() || (info.st_mode & 0077) != 0)
            throw std::runtime_error("private owned capture directory required");
        if (token.size() != 32 || token.find_first_not_of("0123456789abcdef") != std::string::npos)
            throw std::runtime_error("invalid private capture token");
        root = requestedRoot;
        void* library = dlopen(libraryPath.c_str(), RTLD_NOW | RTLD_LOCAL);
        if (!library) throw std::runtime_error(std::string("private capture library: ") + dlerror());
        struct Library { void* value; ~Library() { dlclose(value); } } loaded{library};
        auto hash = reinterpret_cast<Hash>(dlsym(library,
            "_ZN2ga6bridge10sha256FileERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE"));
        if (!hash) throw std::runtime_error("private capture hash symbol unavailable");
        if (mode == "seed-control") { seedControl(root, source, hash); return; }
        auto* document = acDocManager ? acDocManager->curDocument() : nullptr;
        auto* database = document ? document->database() : nullptr;
        const ACHAR* filename = nullptr;
        if (!database || database != acdbHostApplicationServices()->workingDatabase() ||
            database->getFilename(filename) != Acad::eOk || !filename ||
            fs::canonical(AcString(filename).utf8Str()) != source)
            throw std::runtime_error("active drawing is not the owned requested source");
        if (mode == "delivery-parity") {
            DiagnosticEnvironment diagnostics;
            runPrivateDeliveryProbe(library, root.string(), source.string(), libraryPath.string());
            return;
        }
        const auto snapshot = root / ("map-" + token + ".json");
        if (fs::exists(snapshot) || fs::exists(snapshot.string() + ".inventory.json"))
            throw std::runtime_error("private capture output already exists");
        const bool control = mode == "append-point-control" || mode == "move-line-control";
        if (control || mode == "archive-write-control") {
            controlDigest(root, source, hash);
            verifyControl(*database, false);
            if (fs::exists(root / "control-receipt.json"))
                throw std::runtime_error("control receipt already exists");
            if (mode == "archive-write-control" && (fs::exists(root / "Native") ||
                fs::exists(root / "write-ownership-receipt.json")))
                throw std::runtime_error("write ownership output already exists");
        }
        auto open = reinterpret_cast<Open>(dlsym(library,
            "_ZN2ga9liveQuery11openSessionERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEES9_"));
        auto close = reinterpret_cast<Close>(dlsym(library, "_ZN2ga9liveQuery12closeSessionEv"));
        auto inspect = reinterpret_cast<Inspect>(dlsym(library,
            "_ZN2ga9liveQuery14inspectSessionERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE"));
        if (!open || !close || !inspect) throw std::runtime_error("private live session symbols unavailable");
        struct Session { Close close; ~Session() { close(); } } session{close};
        DiagnosticEnvironment diagnostics;
        const auto metadata = open(token, snapshot.string());
        std::ofstream(root / "session-metadata.json") << metadata << '\n';
        if (control)
            editControl(document, *database, root, source, token, mode, inspect, hash);
        if (mode == "archive-write-control") {
            auto archive = reinterpret_cast<Open>(dlsym(library,
                "_ZN2ga9liveQuery14archiveSessionERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEES9_"));
            if (!archive) throw std::runtime_error("native archive symbol unavailable");
            archiveWriteControl(document, *database, root, source, token, inspect, hash, archive);
        }
        acutPrintf(L"\nLIVE_CAPTURE_COMPLETE");
    } catch (const std::exception& error) {
        if (!root.empty()) std::ofstream(root / "native-error.txt", std::ios::app) << error.what() << '\n';
        acutPrintf(L"\nLIVE_CAPTURE_ERROR: %s", AcString(error.what()).kwszPtr());
    }
}
}  // namespace

extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode message, void* id) {
    if (message == AcRx::kInitAppMsg) {
        acrxLoadModule(L"AcGeomentObj.dbx", 0);
        acrxDynamicLinker->loadModule(L"AcBr.dbx", 1);
        acrxDynamicLinker->unlockApplication(id);
        acrxDynamicLinker->registerAppMDIAware(id);
        acedRegCmds->addCommand(L"GA_PRIVATE_LIVE_CAPTURE", L"GALIVECAPTUREPROBE",
                              L"GALIVECAPTUREPROBE", ACRX_CMD_MODAL, run);
    } else if (message == AcRx::kUnloadAppMsg) {
        acedRegCmds->removeGroup(L"GA_PRIVATE_LIVE_CAPTURE");
    }
    return AcRx::kRetOK;
}

// The standalone SDK build compiles one translation unit. Keep parity's
// boundary adapters separate from source/control qualification above.
#include "live_delivery_probe.cpp"
