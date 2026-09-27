// Diagnostic lifecycle A/B: identical production query, separate native DB.
// No geometry repair, save, fallback, GUI document switch or exit override.
#define acrxEntryPoint queryWorkerEntryPoint
#include "../query-worker/entry.cpp"
#undef acrxEntryPoint
#include "dbapserv.h"
#include "acdbxref.h"
#include "acutads.h"
#include "adslib.h"
#include "AcString.h"
#include "native_area_candidates.h"
#include "xref_instance_access.h"
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <memory>
#include <stdexcept>
#include "query_entity_inventory.h"
#include "DbField.h"

namespace {
namespace fs = std::filesystem;
std::string filename(AcDbDatabase* db) {
    const ACHAR* name = nullptr;
    if (!db || db->getFilename(name) != Acad::eOk || !name) return "";
    return AcString(name).utf8Str();
}
void checked(Acad::ErrorStatus result, const char* operation) {
    if (result != Acad::eOk)
        throw std::runtime_error(std::string(operation) + ":" + std::to_string(int(result)));
}
struct FieldEvaluationScope {
    AcDbField::EvalOption previous=acdbGlobalFieldEvaluationOption();
    bool active=false;
    explicit FieldEvaluationScope(bool disable):active(disable) {
        if(active) checked(acdbSetGlobalFieldEvaluationOption(AcDbField::kDisable), "disable field evaluation");
    }
    void restore() {
        if(active) checked(acdbSetGlobalFieldEvaluationOption(previous), "restore field evaluation");
        active=false;
    }
    ~FieldEvaluationScope() { if(active) acdbSetGlobalFieldEvaluationOption(previous); }
};
void graph(const fs::path& path, AcDbDatabase& db) {
    AcDbXrefGraph g;
    checked(acdbGetHostDwgXrefGraph(&db, g, true), "xref graph");
    std::ofstream out(path);
    out << "[";
    for (int i = 0; i < g.numNodes(); ++i) {
        auto* node = g.xrefNode(i);
        out << (i ? "," : "") << "{\"name\":" << std::quoted(AcString(node->name()).utf8Str())
            << ",\"status\":" << int(node->xrefStatus())
            << ",\"nested\":" << (node->isNested() ? "true" : "false")
            << ",\"database_present\":" << (node->database() ? "true" : "false")
            << ",\"database_file\":" << std::quoted(filename(node->database())) << "}";
    }
    out << "]";
    if (!out.good()) throw std::runtime_error("graph publication failed");
}
void sideQuery() {
    ACHAR argument[4096]{};
    if (acedGetString(1, L"\nSide DB lifecycle request: ", argument) != RTNORM) return;
    fs::path root;
    try {
        const auto request = fs::canonical(AcString(argument).utf8Str());
        root = request.parent_path();
        auto* services = acdbHostApplicationServices();
        auto* prior = services->workingDatabase();
        // Only an independently launched Core with our scratch Empty.dwg.
        if (root.string().find("/private/tmp/ga-side-query-") != 0
            || fs::canonical(filename(prior)) != root / "Empty.dwg")
            throw std::runtime_error("private empty host required");
        std::ifstream input(request);
        std::string source;
        std::getline(input, source);
        const auto drawing = fs::canonical(source);
        if (drawing.string().find((root / "package").string() + "/") != 0)
            throw std::runtime_error("private package required");
        FieldEvaluationScope evaluation(fs::exists(root / "field-evaluation-disabled"));
        {
            AcDbDatabase database(false, true);
            struct Restore {
                AcDbHostApplicationServices* services;
                AcDbDatabase* prior;
                ~Restore() { services->setWorkingDatabase(prior); }
            } restore{services, prior};
            checked(database.readDwgFile(AcString(source.c_str()).kwszPtr(),
                                        AcDbDatabase::kForReadAndAllShare, true), "read DWG");
            checked(database.closeInput(true), "close input");
            services->setWorkingDatabase(&database);
            checked(acdbResolveCurrentXRefs(&database, false, false), "resolve XREFs");
            graph(root / "side-graph-before.json", database);
            if(fs::exists(root / "inventory-enabled"))
                ga::diagnostic::inventory(root / "entities-before.json", database);
            // The exact same protocol, identity checks and geometry kernel.
            // Its prompt reads the next SCR line. No different query algorithm.
            ga::nativeQuery::queryObjectsCommand();
            graph(root / "side-graph-after.json", database);
            if(fs::exists(root / "inventory-enabled"))
                ga::diagnostic::inventory(root / "entities-after.json", database);
        }
        evaluation.restore();
        std::ofstream(root / "side-db-destroyed") << "working database restored; side database destroyed\n";
    } catch (const std::exception& error) {
        if (!root.empty()) std::ofstream(root / "side-error.txt") << error.what();
        acutPrintf(L"\nGA_SIDE_DB_ERROR: %s", AcString(error.what()).kwszPtr());
    }
}
void discoverCandidates() {
    ACHAR argument[4096]{};
    if (acedGetString(1, L"\nNative area candidates request: ", argument) != RTNORM) return;
    fs::path root;
    try {
        const auto request = fs::canonical(AcString(argument).utf8Str());
        root = request.parent_path();
        auto* db = acdbHostApplicationServices()->workingDatabase();
        if (root.string().find("/private/tmp/ga-side-query-") != 0
            || fs::canonical(filename(db)).string().find((root / "package").string() + "/") != 0)
            throw std::runtime_error("private document package required");
        std::ifstream input(request);
        std::size_t count = 0;
        if (!(input >> count) || !count || count > 100000)
            throw std::runtime_error("invalid candidate address count");
        ga::nativeQuery::AreaCandidates collector;
        std::ostringstream failures;
        unsigned failed = 0;
        for (std::size_t i = 0; i < count; ++i) {
            std::string route;
            if (!(input >> route)) throw std::runtime_error("missing candidate address");
            try {
                ga::xref::Instance instance;
                ga::xref::resolve(*db, route, instance);
                collector.consider(*instance.entity, route);
            } catch (const std::exception& error) {
                failures << (failed++ ? "," : "") << "{\"route\":" << std::quoted(route)
                         << ",\"error\":" << std::quoted(error.what()) << "}";
            }
        }
        const auto candidates = collector.collect();
        std::ofstream out(root / "area-candidates.json");
        out << "{\"scope\":\"provided full-layer native addresses; candidates require native area validation\","
            << "\"observed\":" << collector.observed << ",\"individually_closed\":" << collector.individuallyClosed
            << ",\"unreadable\":[" << failures.str() << "],\"candidates\":[";
        bool first = true;
        for (const auto& candidate : candidates) {
            out << (first ? "" : ",") << "{\"layer\":" << std::quoted(candidate.layer)
                << ",\"error\":" << std::quoted(candidate.error) << ",\"routes\":[";
            first = false;
            for (std::size_t i = 0; i < candidate.routes.size(); ++i)
                out << (i ? "," : "") << std::quoted(candidate.routes[i]);
            out << "]}";
        }
        out << "]}";
        if (!out.good()) throw std::runtime_error("candidate publication failed");
    } catch (const std::exception& error) {
        if (!root.empty()) std::ofstream(root / "discovery-error.txt") << error.what();
        acutPrintf(L"\nGA_AREA_DISCOVERY_ERROR: %s", AcString(error.what()).kwszPtr());
    }
}
}

extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode message, void* appId) {
    const auto result = queryWorkerEntryPoint(message, appId);
    if (message == AcRx::kInitAppMsg) {
        acedRegCmds->addCommand(L"GA_SIDE_DB_PROBE", L"GASIDEDBQUERY", L"GASIDEDBQUERY",
                               ACRX_CMD_MODAL, sideQuery);
        acedRegCmds->addCommand(L"GA_SIDE_DB_PROBE", L"GADISCOVERAREAS", L"GADISCOVERAREAS",
                               ACRX_CMD_MODAL, discoverCandidates);
    } else if (message == AcRx::kUnloadAppMsg)
        acedRegCmds->removeGroup(L"GA_SIDE_DB_PROBE");
    return result;
}
