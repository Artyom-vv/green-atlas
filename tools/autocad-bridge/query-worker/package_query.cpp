#include "package_query.h"
#include "native_query_command.h"
#include "file_io.h"
#include "dbapserv.h"
#include "dbmain.h"
#include "DbField.h"
#include "acdbxref.h"
#include "aced.h"
#include "adslib.h"
#include "AcString.h"
#include <filesystem>
#include <fstream>
#include <stdexcept>

namespace ga::queryWorker {
namespace {
namespace fs = std::filesystem;
void checked(Acad::ErrorStatus status, const char* operation) {
    if(status != Acad::eOk)
        throw std::runtime_error(std::string(operation) + ":" + std::to_string(int(status)));
}
class FieldEvaluationScope {
    AcDbField::EvalOption previous = acdbGlobalFieldEvaluationOption();
    bool active = false;
public:
    FieldEvaluationScope() {
        // Query the captured state; do not refresh fields while opening it.
        // Keep DEMANDLOAD unchanged: object enablers are still available.
        checked(acdbSetGlobalFieldEvaluationOption(AcDbField::kDisable), "disable field evaluation");
        active = true;
    }
    void restore() {
        checked(acdbSetGlobalFieldEvaluationOption(previous), "restore field evaluation");
        if(acdbGlobalFieldEvaluationOption() != previous)
            throw std::runtime_error("field evaluation option was not restored");
        active = false;
    }
    ~FieldEvaluationScope() { if(active) acdbSetGlobalFieldEvaluationOption(previous); }
};
struct WorkingDatabaseScope {
    AcDbHostApplicationServices* services;
    AcDbDatabase* previous;
    ~WorkingDatabaseScope() { services->setWorkingDatabase(previous); }
};
fs::path databaseFile(AcDbDatabase* database) {
    const ACHAR* name = nullptr;
    if(!database) throw std::runtime_error("missing bootstrap database");
    checked(database->getFilename(name), "bootstrap filename");
    if(!name) throw std::runtime_error("missing bootstrap filename");
    return fs::canonical(AcString(name).utf8Str());
}
}

void queryPackageCommand() {
    try {
        auto* services = acdbHostApplicationServices();
        auto* previous = services->workingDatabase();
        const auto bootstrap = databaseFile(previous);
        const auto root = bootstrap.parent_path();
        // SCR encoding is locale-dependent. The supervisor writes paths as
        // UTF-8 data, never as non-ASCII command text.
        std::ifstream input(root / "package-entry.txt");
        std::string entry, extra;
        if(!std::getline(input, entry) || entry.empty() || entry.size() > 4096
            || std::getline(input, extra))
            throw std::runtime_error("invalid UTF-8 package entry");
        const auto drawing = fs::canonical(entry);
        const auto relative = drawing.lexically_relative(root / "package");
        if(bootstrap.filename() != "Bootstrap.dwg"
            || root.filename().string().rfind("ga-native-query-", 0) != 0
            || relative.empty() || *relative.begin() == ".."
            || (drawing.extension() != ".dwg" && drawing.extension() != ".DWG"))
            throw std::runtime_error("private captured DWG package required");
        const auto request = root / "request.txt";
        const auto requestHash = ga::bridge::sha256File(request.string());
        const auto sourceHash = ga::bridge::sha256File(drawing.string());
        if(requestHash.empty() || sourceHash.empty() || fs::exists(root / "lifecycle.json"))
            throw std::runtime_error("invalid package query files");
        FieldEvaluationScope fields;
        {
            AcDbDatabase database(false, true);
            // Restore the context BEFORE destroying its database, including
            // failures. Field evaluation stays disabled until after deletion.
            WorkingDatabaseScope context{services, previous};
            checked(database.readDwgFile(AcString(drawing.string().c_str()).kwszPtr(),
                AcDbDatabase::kForReadAndAllShare, true), "read captured DWG");
            checked(database.closeInput(true), "close captured input");
            services->setWorkingDatabase(&database);
            checked(acdbResolveCurrentXRefs(&database, false, false), "resolve captured XREFs");
            ga::nativeQuery::queryObjectsFile(request.string());
        }
        fields.restore();
        if(services->workingDatabase() != previous
            || requestHash != ga::bridge::sha256File(request.string())
            || sourceHash != ga::bridge::sha256File(drawing.string()))
            throw std::runtime_error("package lifecycle identity changed");
        const auto replyHash = ga::bridge::sha256File((root / "native.json").string());
        if(replyHash.empty()) throw std::runtime_error("missing package reply");
        // This receipt is published after cleanup, not merely after queries.
        // The supervisor still requires exit 0 and verifies package bytes.
        const std::string receipt = "{\"schema\":\"green-atlas.native-query-lifecycle/1\","
            "\"request_sha256\":\"" + requestHash + "\",\"source_sha256\":\"" + sourceHash
            + "\",\"reply_sha256\":\"" + replyHash + "\",\"database_destroyed\":true,"
            "\"working_database_restored\":true,\"field_evaluation_restored\":true}";
        if(!ga::bridge::writeAtomicText((root / "lifecycle.json").string(), receipt))
            throw std::runtime_error("lifecycle publication failed");
    } catch(const std::exception& error) {
        acutPrintf(L"\nGreen Atlas: package query failed: %s", AcString(error.what()).kwszPtr());
    }
}
}
