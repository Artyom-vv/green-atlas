// Experimental file intake. Runs only in an owned Core process, never the GUI.
// Reuse the production capture and the existing independent inspection oracle.
#define GA_PRODUCT_CAPTURE_ENTRY previousProductProbeEntry
#include "product_capture_probe.cpp"
#undef GA_PRODUCT_CAPTURE_ENTRY
#include "DbField.h"
#include "instance_contract_fixture.h"

namespace {
void captureDatabase(AcDbDatabase* host, const fs::path& root, const std::string& action) {
    const auto file = fs::canonical(filename(host));
    const auto permitted = root / (std::string(action) == "inspect" ? "package" : "source");
    const auto relative = file.lexically_relative(permitted);
    if (relative.empty() || *relative.begin() == "..")
        throw std::runtime_error("drawing outside private intake package");
    if (std::string(action) == "inspect") write(root / "reopened.json", snapshot(host));
    else if (action == "archive") {
        write(root / "archive-opened.json", snapshot(host));
        const auto displaySha = ga::bridge::sha256File((root / "Drawing.autocad.json").string());
        if (displaySha.empty()) throw std::runtime_error("missing verified display for saved-file archive");
        ga::capture::capturePackage(*host, (root / "package").string(), displaySha);
    }
    else if (std::string(action) == "capture") {
        write(root / "before.json", snapshot(host));
        // Do not capture loaded external files outside the supplied package.
        AcDbXrefGraph graph;
        ok(acdbGetHostDwgXrefGraph(host, graph, true), "intake XREF graph");
        for (int i = 1; i < graph.numNodes(); ++i) {
            auto* db = graph.xrefNode(i)->database();
            if (!db) continue;
            const auto original = fs::weakly_canonical(utf(db->originalFileName()));
            const auto local = original.lexically_relative(root / "source");
            if (local.empty() || *local.begin() == "..")
                throw std::runtime_error("loaded XREF outside supplied package: " + original.string());
        }
        const auto displaySha = captureProductionInputs(*host, root);
        if (displaySha.empty()) throw std::runtime_error("production capture module required");
        const auto* split = std::getenv("GA_CORE_INTAKE_SPLIT_JOBS");
        if (!split || std::string(split) != "1")
            ga::capture::capturePackage(*host, (root / "package").string(), displaySha);
        write(root / "after.json", snapshot(host));
    } else throw std::runtime_error("unknown intake action");
}

void intakeCommand() {
    const auto* directory = std::getenv("GA_CORE_INTAKE_ROOT");
    const auto* action = std::getenv("GA_CORE_INTAKE_ACTION");
    if (!directory || !action) return;
    const auto root = fs::canonical(directory);
    try {
        if (root.string().find("/output/core-intake-") == std::string::npos)
            throw std::runtime_error("private Core intake experiment required");
        if (std::string(action) == "seed") {
            const auto* variant = std::getenv("GA_CORE_INTAKE_FIXTURE");
            seed(root, !variant || std::string(variant) != "unloaded");
            if (variant && std::string(variant) == "instance-contract") seedInstanceContract(root);
            // Synthetic controls use portable relative links. Never repath user DWG.
            for (const auto& file : fs::directory_iterator(root / "source")) {
                AcDbDatabase db(false, true);
                ok(db.readDwgFile(AcString(file.path().string().c_str()).kwszPtr()), "fixture read");
                db.closeInput(true);
                auto table = open<AcDbBlockTable>(db.blockTableId());
                AcDbBlockTableIterator* raw = nullptr;
                ok(table->newIterator(raw), "fixture iterator");
                std::unique_ptr<AcDbBlockTableIterator> it(raw);
                for (; !it->done(); it->step()) {
                    AcDbObjectId id; ok(it->getRecordId(id), "fixture id");
                    auto record = open<AcDbBlockTableRecord>(id, AcDb::kForWrite);
                    if (!record->isFromExternalReference()) continue;
                    AcString path; record->pathName(path);
                    const auto relative = "./" + fs::path(utf(path.kwszPtr())).filename().string();
                    ok(record->setPathName(AcString(relative.c_str()).kwszPtr()), "fixture relative path");
                }
                it.reset(); table.reset();
                ok(db.saveAs(AcString(file.path().string().c_str()).kwszPtr(), false, AcDb::kDHL_CURRENT), "fixture save");
            }
        } else {
            auto* services = acdbHostApplicationServices();
            auto* previous = services->workingDatabase();
            const auto* mode = std::getenv("GA_CORE_INTAKE_DATABASE_MODE");
            if (mode && std::string(mode) == "active") {
                captureDatabase(previous, root, action);
            } else {
                const auto* source = std::getenv("GA_CORE_INTAKE_DRAWING");
                if (!source || !*source) throw std::runtime_error("missing intake drawing");
                const auto file = fs::canonical(source);
                const auto permitted = root / (std::string(action) == "inspect" ? "package" : "source");
                const auto relative = file.lexically_relative(permitted);
                if (relative.empty() || *relative.begin() == "..")
                    throw std::runtime_error("drawing outside private intake package");
                struct Fields {
                    AcDbField::EvalOption previous = acdbGlobalFieldEvaluationOption();
                    Fields() { ok(acdbSetGlobalFieldEvaluationOption(AcDbField::kDisable), "disable field evaluation"); }
                    ~Fields() { acdbSetGlobalFieldEvaluationOption(previous); }
                } fields;
                {
                    AcDbDatabase database(false, true);
                    struct Context {
                        AcDbHostApplicationServices* services; AcDbDatabase* previous;
                        ~Context() { services->setWorkingDatabase(previous); }
                    } context{services, previous};
                    ok(database.readDwgFile(AcString(file.string().c_str()).kwszPtr(),
                        AcDbDatabase::kForReadAndAllShare, true), "read intake drawing");
                    ok(database.closeInput(true), "close intake input");
                    services->setWorkingDatabase(&database);
                    ok(acdbResolveCurrentXRefs(&database, false, false), "resolve intake XREFs");
                    captureDatabase(&database, root, action);
                }
                ok(acdbSetGlobalFieldEvaluationOption(fields.previous), "restore field evaluation");
                if (services->workingDatabase() != previous || acdbGlobalFieldEvaluationOption() != fields.previous)
                    throw std::runtime_error("intake lifecycle not restored");
                write(root / (std::string(action) + "-lifecycle.json"),
                    "{\"database_destroyed\":true,\"working_database_restored\":true,\"field_evaluation_restored\":true}");
            }
        }
        write(root / (std::string(action) + "-native-completed.json"), "{\"completed\":true}");
    } catch (const std::exception& error) {
        write(root / (std::string(action) + "-native-error.json"), "{\"error\":" + q(error.what()) + "}");
    }
}
}

extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode message, void* appId) {
    if (message == AcRx::kInitAppMsg) {
        acrxLoadModule(L"AcGeomentObj.dbx", 0);
        acrxDynamicLinker->loadModule(L"AcBr.dbx", 1);
        acrxDynamicLinker->unlockApplication(appId);
        acrxDynamicLinker->registerAppMDIAware(appId);
        acedRegCmds->addCommand(L"GA_CORE_INTAKE_EXPERIMENT", L"GAINTAKEFILE", L"GAINTAKEFILE", ACRX_CMD_MODAL, intakeCommand);
    } else if (message == AcRx::kUnloadAppMsg) acedRegCmds->removeGroup(L"GA_CORE_INTAKE_EXPERIMENT");
    return AcRx::kRetOK;
}
