// Experiment only: distance to native BRep edge set, including wire/spur edges.
// Not distance to occupied area; inside/outside must be interpreted separately.
#include "distance_probe.h"
#include "brbrep.h"
#include "brbetrav.h"
#include "bredge.h"
#include "gecurv3d.h"
#include "geponc3d.h"
#include "geintrvl.h"
#include "getol.h"
#include <chrono>
#include <cmath>
#include <limits>
#include <memory>
#include <ostream>

namespace {
constexpr unsigned kEdgeBudget = 4096;
constexpr double kQueryTolerance = 1e-8; // Drawing units, NOT a planting rule.
bool finite(const AcGePoint3d& p) {
    return std::isfinite(p.x) && std::isfinite(p.y) && std::isfinite(p.z);
}
void point(std::ostream& out, const AcGePoint3d& p) {
    out << '[' << p.x << ',' << p.y << ',' << p.z << ']';
}
}

void emitDistanceProbe(std::ostream& out, const AcBrBrep& brep,
                       const std::vector<AcGePoint3d>& points) {
    const auto started = std::chrono::steady_clock::now();
    AcBrBrepEdgeTraverser traversal;
    auto status = traversal.setBrep(brep);
    bool complete = status == AcBr::eOk;
    std::vector<std::unique_ptr<AcGeCurve3d>> curves;
    out << ",\"distance_probe\":{\"meaning\":\"native_edge_set_not_occupied_area\","
        << "\"tolerance_units\":" << kQueryTolerance
        << ",\"traversal_start_status\":" << int(status) << ",\"edges\":[";
    unsigned n = 0;
    while (status == AcBr::eOk && !traversal.done() && n < kEdgeBudget) {
        if (n++) out << ',';
        AcBrEdge edge;
        const auto opened = traversal.getEdge(edge);
        AcGeCurve3d* raw = nullptr;
        const auto extracted = opened == AcBr::eOk ? edge.getCurve(raw) : opened;
        std::unique_ptr<AcGeCurve3d> curve(raw);
        bool bounded = false;
        if (extracted == AcBr::eOk && curve) {
            AcGeInterval interval;
            curve->getInterval(interval);
            bounded = interval.isBounded() && std::isfinite(interval.lowerBound())
                && std::isfinite(interval.upperBound());
        }
        out << "{\"open_status\":" << int(opened) << ",\"curve_status\":" << int(extracted)
            << ",\"finite_interval\":" << (bounded ? "true" : "false") << '}';
        if (bounded) curves.push_back(std::move(curve));
        else { complete = false; curves.push_back(nullptr); }
        status = traversal.next();
    }
    complete = complete && status == AcBr::eOk && traversal.done() && !curves.empty();
    out << "],\"traversal_end_status\":" << int(status)
        << ",\"edge_set_complete\":" << (complete ? "true" : "false") << ",\"queries\":[";
    AcGeTol tolerance;
    tolerance.setEqualPoint(kQueryTolerance);
    tolerance.setEqualVector(kQueryTolerance);
    for (std::size_t pi = 0; pi < points.size(); ++pi) {
        if (pi) out << ',';
        double best = std::numeric_limits<double>::infinity();
        AcGePoint3d nearest;
        std::size_t nearestEdge = 0;
        unsigned failed = 0;
        for (std::size_t i = 0; i < curves.size(); ++i) {
            if (!curves[i]) { ++failed; continue; }
            try {
                AcGePointOnCurve3d answer;
                curves[i]->getClosestPointTo(points[pi], answer, tolerance);
                const auto candidate = answer.point();
                const double parameter = answer.parameter();
                AcGeInterval interval;
                curves[i]->getInterval(interval);
                if (!finite(candidate) || !std::isfinite(parameter) || !interval.contains(parameter)) {
                    ++failed; continue;
                }
                const double distance = points[pi].distanceTo(candidate);
                if (!std::isfinite(distance)) { ++failed; continue; }
                if (distance < best) { best = distance; nearest = candidate; nearestEdge = i; }
            } catch (...) { ++failed; }
        }
        const bool valid = complete && !failed && std::isfinite(best);
        out << "{\"point\":"; point(out, points[pi]);
        out << ",\"failed_edge_queries\":" << failed
            << ",\"complete\":" << (valid ? "true" : "false") << ",\"distance_units\":";
        if (valid) out << best; else out << "null";
        // Partial minima are evidence only, never an accepted clearance.
        out << ",\"observed_partial_minimum\":";
        if (std::isfinite(best)) {
            out << best << ",\"nearest_edge_index\":" << nearestEdge << ",\"nearest_point\":";
            point(out, nearest);
        } else out << "null";
        out << '}';
    }
    out << "],\"elapsed_ms\":" << std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - started).count() << '}';
}
