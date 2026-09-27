// Isolated native-query experiment. No snapshot compiler, sampled polygon,
// source edits, CAD saves, UI navigation, or product publication.
#include "direct_query_kernel.h"
#include "file_io.h"
#include "rxregsvc.h"
#include "core_rxmfcapi.h"
#include "aced.h"
#include "adslib.h"
#include "AcString.h"
#include "geblok3d.h"
#include "geintrvl.h"
#include <sys/resource.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace {
using Clock = std::chrono::steady_clock;
using ga::direct::Answer;
std::string quote(const std::string& s) { return "\"" + ga::bridge::jsonEscape(s) + "\""; }
double elapsed(Clock::time_point start) {
    return std::chrono::duration<double, std::milli>(Clock::now() - start).count();
}
std::string line(std::istream& in) {
    std::string s;
    if (!std::getline(in, s)) throw std::runtime_error("truncated request");
    if (!s.empty() && s.back() == '\r') s.pop_back();
    return s;
}
int integer(std::istream& in, int low, int high) {
    const auto s = line(in); std::size_t end;
    const int n = std::stoi(s, &end);
    if (end != s.size() || n < low || n > high) throw std::runtime_error("invalid bounded integer");
    return n;
}
struct Control { AcGePoint3d point; std::string expected, label; };
struct Case {
    std::string name, handle;
    std::vector<std::string> chain;
    int grid = 0, repeats = 0;
    double clearance = 0;
    std::vector<Control> controls;
};
struct Query { AcGePoint3d point; std::string expected, label; bool onEdge = false; };
void xyz(std::ostream& out, const AcGePoint3d& p) {
    out << '[' << p.x << ',' << p.y << ',' << p.z << ']';
}
std::string decision(const Answer& answer, double clearance) {
    if (answer.status != 0 || answer.membership == "unknown") return "unknown";
    if (answer.membership != "outside") return "blocked_inside_or_edge";
    if (!answer.distanceComplete) return "unknown";
    if (answer.distance < clearance) return "blocked_clearance";
    return "clear_of_selected_object_only";
}
void bounds(const AcBrBrep& brep, AcGePoint3d& low, AcGePoint3d& high) {
    AcGeBoundBlock3d block;
    const auto status = brep.getBoundBlock(block);
    if (status != AcBr::eOk) throw std::runtime_error("native bound status " + std::to_string(int(status)));
    block.getMinMaxPoints(low, high);
}
void runCase(AcDbDatabase& database, const Case& item, std::ostream& out) {
    ga::direct::Prepared prepared;
    ga::direct::prepare(database, item.handle, item.chain, prepared);
    AcGePoint3d low, high;
    bounds(prepared.local, low, high);
    if (std::abs(high.z - low.z) > 1e-7)
        throw std::runtime_error("local grid requires a horizontal planar area in this experiment");
    std::vector<Query> queries;
    for (const auto& control : item.controls) {
        auto point = control.point;
        queries.push_back({point.transformBy(prepared.transform), control.expected, control.label});
    }
    const double margin = std::max(1.0, item.clearance + 0.5);
    for (int x = 0; x < item.grid; ++x) for (int y = 0; y < item.grid; ++y) {
        AcGePoint3d p(low.x - margin + (high.x - low.x + 2 * margin) * x / (item.grid - 1),
                      low.y - margin + (high.y - low.y + 2 * margin) * y / (item.grid - 1), low.z);
        queries.push_back({p.transformBy(prepared.transform), "discovery", "grid"});
    }
    // Native curve evaluation gives exact edge witnesses, no tessellation.
    for (std::size_t i = 0; i < std::min<std::size_t>(4, prepared.curves.size()); ++i) {
        AcGeInterval interval; prepared.curves[i]->getInterval(interval);
        queries.push_back({prepared.curves[i]->evalPoint((interval.lowerBound() + interval.upperBound()) / 2),
                           "edge", "native_edge_midparameter", true});
    }
    AcGePoint3d worldLow, worldHigh;
    bounds(prepared.world, worldLow, worldHigh);
    queries.push_back({{worldHigh.x + margin, worldHigh.y + margin, worldHigh.z},
                       "outside", "outside_native_world_bounds"});
    std::vector<Answer> first;
    bool repeated = true; double maxDelta = 0;
    std::vector<double> durations, containmentDurations, distanceDurations;
    for (int repeat = 0; repeat < item.repeats; ++repeat) {
        const auto started = Clock::now();
        double containmentMs = 0, distanceMs = 0;
        for (std::size_t i = 0; i < queries.size(); ++i) {
            const auto answer = ga::direct::query(prepared, queries[i].point);
            containmentMs += answer.containmentMs; distanceMs += answer.distanceMs;
            if (!repeat) first.push_back(answer);
            else repeated = ga::direct::stable(first[i], answer, maxDelta) && repeated;
        }
        durations.push_back(elapsed(started));
        containmentDurations.push_back(containmentMs); distanceDurations.push_back(distanceMs);
    }
    unsigned alternativeMismatches = 0;
    double alternativeDelta = 0;
    std::vector<double> alternativeMs;
    for (int repeat = 0; repeat < item.repeats; ++repeat) {
        const auto started = Clock::now();
        for (std::size_t i = 0; i < queries.size(); ++i) {
            const auto answer = ga::direct::query(prepared, queries[i].point, true);
            if (!ga::direct::stable(first[i], answer, alternativeDelta)) ++alternativeMismatches;
        }
        alternativeMs.push_back(elapsed(started));
    }
    unsigned gelibMismatches = 0;
    double gelibDelta = 0;
    std::vector<double> gelibMs;
    for (int repeat = 0; repeat < item.repeats; ++repeat) {
        const auto started = Clock::now();
        for (std::size_t i = 0; i < queries.size(); ++i) {
            const auto answer = ga::direct::query(prepared, queries[i].point, false, true);
            if (!ga::direct::stable(first[i], answer, gelibDelta)) ++gelibMismatches;
        }
        gelibMs.push_back(elapsed(started));
    }
    unsigned passed = 0, failed = 0, unknown = 0, occupied = 0, clearance = 0, clear = 0;
    unsigned covarianceMismatches = 0;
    const auto inverse = prepared.transform.inverse();
    // Compare the API's full instance path with its definition space. This is
    // a transform consistency control, not an independent shape oracle.
    for (std::size_t i = 0; i < queries.size(); ++i) {
        auto localPoint = queries[i].point; localPoint.transformBy(inverse);
        const auto local = ga::direct::membership(prepared.local, localPoint);
        if (local.status != first[i].status || local.membership != first[i].membership)
            ++covarianceMismatches;
    }
    out << "{\"name\":" << quote(item.name) << ",\"handle\":" << quote(item.handle)
        << ",\"entity_type\":" << quote(prepared.entityType) << ",\"layer\":" << quote(prepared.layer)
        << ",\"method\":" << quote(prepared.method) << ",\"chain\":[";
    for (std::size_t i = 0; i < item.chain.size(); ++i) out << (i ? "," : "") << quote(item.chain[i]);
    out << "],\"instance_transform\":[";
    for (int r = 0; r < 4; ++r) for (int c = 0; c < 4; ++c)
        out << (r || c ? "," : "") << prepared.transform.entry[r][c];
    out << "],\"local_bounds\":["; xyz(out, low); out << ','; xyz(out, high);
    out << "],\"world_bounds\":["; xyz(out, worldLow); out << ','; xyz(out, worldHigh);
    out << "],\"native_edges\":" << prepared.curves.size() << ",\"prepare_ms\":" << prepared.setupMs
        << ",\"clearance_units_experimental_not_normative\":" << item.clearance
        << ",\"query_count\":" << queries.size() << ",\"repeats\":" << item.repeats
        << ",\"batch_ms\":[";
    for (std::size_t i = 0; i < durations.size(); ++i) out << (i ? "," : "") << durations[i];
    out << "],\"batch_native_containment_ms\":[";
    for (std::size_t i = 0; i < durations.size(); ++i) out << (i ? "," : "") << containmentDurations[i];
    out << "],\"batch_native_closest_point_ms\":[";
    for (std::size_t i = 0; i < durations.size(); ++i) out << (i ? "," : "") << distanceDurations[i];
    out << "],\"alternative_closestPointTo_batch_ms\":[";
    for (std::size_t i = 0; i < alternativeMs.size(); ++i) out << (i ? "," : "") << alternativeMs[i];
    out << "],\"alternative_mismatches\":" << alternativeMismatches
        << ",\"alternative_max_distance_delta\":" << alternativeDelta;
    out << ",\"exact_gelib_curves\":" << prepared.exactGelibCurves << ",\"gelib_batch_ms\":[";
    for (std::size_t i = 0; i < gelibMs.size(); ++i) out << (i ? "," : "") << gelibMs[i];
    out << "],\"gelib_mismatches\":" << gelibMismatches << ",\"gelib_max_distance_delta\":" << gelibDelta;
    out << ",\"repeat_stable\":" << (repeated ? "true" : "false")
        << ",\"max_repeat_distance_delta\":" << maxDelta
        << ",\"instance_definition_membership_mismatches\":" << covarianceMismatches
        << ",\"answers\":[";
    for (std::size_t i = 0; i < first.size(); ++i) {
        const auto& a = first[i]; const auto& q = queries[i];
        const auto verdict = decision(a, item.clearance);
        if (verdict == "unknown") ++unknown;
        else if (verdict == "blocked_inside_or_edge") ++occupied;
        else if (verdict == "blocked_clearance") ++clearance;
        else ++clear;
        if (q.expected != "discovery") {
            const bool ok = a.status == 0 && a.membership == q.expected
                && a.distanceComplete && (!q.onEdge || a.distance <= 1e-7);
            if (ok) ++passed; else ++failed;
        }
        out << (i ? "," : "") << "{\"point\":"; xyz(out, q.point);
        out << ",\"label\":" << quote(q.label) << ",\"expected\":" << quote(q.expected)
            << ",\"status\":" << a.status << ",\"membership\":" << quote(a.membership)
            << ",\"container\":" << quote(a.container) << ",\"distance_complete\":"
            << (a.distanceComplete ? "true" : "false") << ",\"distance_units\":";
        if (a.distanceComplete) out << a.distance; else out << "null";
        out << ",\"nearest_point\":";
        if (a.distanceComplete) xyz(out, a.nearest); else out << "null";
        out << ",\"decision\":" << quote(verdict) << '}';
    }
    out << "],\"controls_passed\":" << passed << ",\"controls_failed\":" << failed
        << ",\"unknown\":" << unknown << ",\"blocked_inside_or_edge\":" << occupied
        << ",\"blocked_clearance\":" << clearance << ",\"clear_of_selected_object_only\":" << clear << '}';
}
void run() {
    ACHAR requestPath[4096] = {};
    if (acedGetString(1, _T("\nDirect query request: "), requestPath) != RTNORM) return;
    try {
        std::ifstream input(AcString(requestPath).utf8Str());
        const std::string source = line(input), output = line(input);
        if (std::ifstream(output).good()) throw std::runtime_error("output already exists");
        const int casesCount = integer(input, 1, 16);
        std::vector<Case> cases;
        for (int i = 0; i < casesCount; ++i) {
            Case item; item.name = line(input); item.handle = line(input);
            const int chainCount = integer(input, 0, 16);
            for (int j = 0; j < chainCount; ++j) item.chain.push_back(line(input));
            item.grid = integer(input, 2, 80); item.repeats = integer(input, 1, 10);
            item.clearance = std::stod(line(input));
            if (!std::isfinite(item.clearance) || item.clearance < 0 || item.clearance > 100)
                throw std::runtime_error("invalid experimental clearance");
            const int controls = integer(input, 0, 64);
            for (int j = 0; j < controls; ++j) {
                std::istringstream row(line(input)); double x, y, z; Control control;
                if (!(row >> x >> y >> z >> control.expected >> control.label)
                    || !std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z))
                    throw std::runtime_error("invalid control");
                control.point = {x, y, z}; item.controls.push_back(control);
            }
            cases.push_back(item);
        }
        const auto before = ga::bridge::sha256File(source);
        if (before.empty()) throw std::runtime_error("source hash unavailable");
        AcDbDatabase database(false, true);
        auto* services = acdbHostApplicationServices();
        struct Restore { AcDbHostApplicationServices* services; AcDbDatabase* prior;
            ~Restore() { services->setWorkingDatabase(prior); }
        } restore{services, services->workingDatabase()};
        const auto loadStart = Clock::now();
        const auto status = source.size() >= 4 && source.substr(source.size() - 4) == ".dxf"
            ? database.dxfIn(AcString(source.c_str()).kwszPtr())
            : database.readDwgFile(AcString(source.c_str()).kwszPtr(), AcDbDatabase::kForReadAndAllShare, true);
        const double loadMs = elapsed(loadStart);
        if (status != Acad::eOk) throw std::runtime_error("native open status " + std::to_string(int(status)));
        services->setWorkingDatabase(&database);
        std::ostringstream out; out << std::setprecision(17);
        out << "{\"schema\":\"green-atlas.direct-query-experiment/1\",\"source\":" << quote(source)
            << ",\"source_sha256\":" << quote(before) << ",\"units_code\":" << int(database.insunits())
            << ",\"native_database_load_ms\":" << loadMs << ",\"cases\":[";
        for (std::size_t i = 0; i < cases.size(); ++i) {
            if (i) out << ',';
            std::ostringstream result; result << std::setprecision(17);
            try { runCase(database, cases[i], result); out << result.str(); }
            catch (const std::exception& e) {
                out << "{\"name\":" << quote(cases[i].name) << ",\"error\":" << quote(e.what()) << '}';
            }
        }
        struct rusage usage {}; getrusage(RUSAGE_SELF, &usage);
        out << "],\"process_peak_rss_bytes_macos\":" << usage.ru_maxrss
            << ",\"source_unchanged\":" << (before == ga::bridge::sha256File(source) ? "true" : "false") << '}';
        if (!ga::bridge::writeAtomicText(output, out.str())) throw std::runtime_error("report write failed");
        acutPrintf(_T("\nDirect query report: %s"), AcString(output.c_str()).kwszPtr());
    } catch (const std::exception& e) {
        acutPrintf(_T("\nDirect query failed: %s"), AcString(e.what()).kwszPtr());
    }
}
}
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode message, void* appId) {
    if (message == AcRx::kInitAppMsg) {
        acrxLoadModule(_T("AcGeomentObj.dbx"), 0);
        acrxDynamicLinker->loadModule(_T("AcBr.dbx"), 1);
        acrxDynamicLinker->unlockApplication(appId);
        acrxDynamicLinker->registerAppMDIAware(appId);
        acedRegCmds->addCommand(_T("GA_DIRECT_QUERY_DIAGNOSTIC"), _T("GADIRECTFILE"),
                               _T("GADIRECTFILE"), ACRX_CMD_MODAL, run);
    } else if (message == AcRx::kUnloadAppMsg) acedRegCmds->removeGroup(_T("GA_DIRECT_QUERY_DIAGNOSTIC"));
    return AcRx::kRetOK;
}
