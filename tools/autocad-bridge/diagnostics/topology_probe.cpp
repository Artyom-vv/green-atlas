// Enumerate native topology without the production single-face restriction.
// Samples only help select test points; they are NOT exact boundary geometry
// and must not be used as a replacement CAD importer or acceptance oracle.
#include "topology_probe.h"
#ifdef GA_SEAM_PROBE
#include "edge_probe.h"
#endif
#include "brbrep.h"
#include "brbftrav.h"
#include "brface.h"
#include "brfltrav.h"
#include "brloop.h"
#include "brletrav.h"
#include "gecurv3d.h"
#include "geintrvl.h"
#include "geblok3d.h"
#include <cmath>
#include <memory>
#include <ostream>

namespace {
constexpr unsigned kTopologyBudget = 4096;
constexpr unsigned kSegmentsPerEdge = 16;
void point(std::ostream& out, const AcGePoint3d& p) {
    out << '[' << p.x << ',' << p.y << ',' << p.z << ']';
}
bool finite(const AcGePoint3d& p) {
    return std::isfinite(p.x) && std::isfinite(p.y) && std::isfinite(p.z);
}
void bounds(std::ostream& out, const AcBrEntity& entity) {
    AcGeBoundBlock3d block;
    const auto status = entity.getBoundBlock(block);
    out << "\"bounds_status\":" << int(status) << ",\"bounds\":";
    AcGePoint3d low, high;
    if (status == AcBr::eOk) block.getMinMaxPoints(low, high);
    if (status == AcBr::eOk && finite(low) && finite(high)) {
        out << '['; point(out, low); out << ','; point(out, high); out << ']';
    } else out << "null";
}
const char* loopName(AcBr::LoopType type) {
    if (type == AcBr::kLoopExterior) return "outer";
    if (type == AcBr::kLoopInterior) return "hole";
    return "unclassified";
}
void edges(std::ostream& out, const AcBrFaceLoopTraverser& loops, unsigned& budget) {
    AcBrLoopEdgeTraverser traversal;
    auto status = traversal.setLoop(loops);
    out << "\"edge_set_status\":" << int(status) << ",\"edges\":[";
    unsigned n = 0;
    bool complete = status == AcBr::eOk;
#ifdef GA_SEAM_PROBE
    std::vector<AcBrVertex> identities;
#endif
    while (status == AcBr::eOk && !traversal.done() && budget) {
        --budget;
        if (n++) out << ',';
        AcGeCurve3d* raw = nullptr;
        const auto curveStatus = traversal.getOrientedCurve(raw);
        std::unique_ptr<AcGeCurve3d> curve(raw);
        out << "{\"curve_status\":" << int(curveStatus);
#ifdef GA_SEAM_PROBE
        emitEdgeProbe(out, traversal, curveStatus == AcBr::eOk ? curve.get() : nullptr, identities);
#endif
        out << ",\"samples\":[";
        bool sampled = false;
        if (curveStatus == AcBr::eOk && curve) {
            AcGeInterval interval;
            curve->getInterval(interval);
            if (interval.isBounded() && std::isfinite(interval.lowerBound()) &&
                std::isfinite(interval.upperBound())) {
                sampled = true;
                for (unsigned j = 0; j <= kSegmentsPerEdge; ++j) {
                    if (j) out << ',';
                    const auto p = curve->evalPoint(interval.lowerBound() +
                        (interval.upperBound() - interval.lowerBound()) *
                        (double(j) / kSegmentsPerEdge));
                    if (finite(p)) point(out, p);
                    else { out << "null"; sampled = false; }
                }
            }
        }
        complete = complete && sampled;
        out << "],\"sampled\":" << (sampled ? "true" : "false") << '}';
        status = traversal.next();
    }
    complete = complete && status == AcBr::eOk && traversal.done();
    out << "],\"edge_end_status\":" << int(status)
        << ",\"samples_complete\":" << (complete ? "true" : "false");
}
void faceLoops(std::ostream& out, const AcBrFace& face, unsigned& budget) {
    AcBrFaceLoopTraverser traversal;
    auto status = traversal.setFace(face);
    out << ",\"loop_set_status\":" << int(status) << ",\"loops\":[";
    unsigned n = 0;
    while (status == AcBr::eOk && !traversal.done() && budget) {
        --budget;
        if (n++) out << ',';
        AcBrLoop loop;
        const auto opened = traversal.getLoop(loop);
        out << "{\"loop_status\":" << int(opened);
        if (opened == AcBr::eOk) {
            AcBr::LoopType type = AcBr::kLoopUnclassified;
            const auto typed = loop.getType(type);
            out << ",\"type_status\":" << int(typed) << ",\"role\":\""
                << (typed == AcBr::eOk ? loopName(type) : "unknown") << "\",";
            edges(out, traversal, budget);
        }
        out << '}';
        status = traversal.next();
    }
    out << "],\"loop_end_status\":" << int(status)
        << ",\"loops_traversed\":"
        << (status == AcBr::eOk && traversal.done() ? "true" : "false");
}
} // namespace

void emitTopologyProbe(std::ostream& out, const AcBrBrep& brep) {
    out << ",\"topology_probe\":{\"samples_are_fixture_hints_only\":true,"
        << "\"segments_per_edge\":" << kSegmentsPerEdge << ',';
    bounds(out, brep);
    out << ",\"measurements\":[";
    unsigned measurement = 0;
    for (double requested : {1e-10, 1e-6, 0.0}) {
        if (measurement++) out << ',';
        double area = 0.0, perimeter = 0.0, areaTolerance = 0.0, lengthTolerance = 0.0;
        const auto areaStatus = brep.getSurfaceArea(area, requested, areaTolerance);
        const auto lengthStatus = brep.getPerimeterLength(perimeter, requested, lengthTolerance);
        out << "{\"requested_tolerance\":" << requested
            << ",\"area_status\":" << int(areaStatus) << ",\"area\":" << area
            << ",\"area_tolerance\":" << areaTolerance
            << ",\"perimeter_status\":" << int(lengthStatus) << ",\"perimeter\":" << perimeter
            << ",\"perimeter_tolerance\":" << lengthTolerance << '}';
    }
    out << ']';
    AcBrBrepFaceTraverser traversal;
    auto status = traversal.setBrep(brep);
    out << ",\"face_set_status\":" << int(status) << ",\"faces\":[";
    unsigned n = 0, budget = kTopologyBudget;
    while (status == AcBr::eOk && !traversal.done() && budget) {
        --budget;
        if (n++) out << ',';
        AcBrFace face;
        const auto opened = traversal.getFace(face);
        out << "{\"face_status\":" << int(opened);
        if (opened == AcBr::eOk) {
            out << ','; bounds(out, face);
            faceLoops(out, face, budget);
        }
        out << '}';
        status = traversal.next();
    }
    out << "],\"face_end_status\":" << int(status)
        << ",\"faces_traversed\":"
        << (status == AcBr::eOk && traversal.done() ? "true" : "false")
        << ",\"budget_remaining\":" << budget << '}';
}
