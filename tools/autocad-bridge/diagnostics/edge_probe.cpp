// Native adjacency evidence only. No endpoint repair or production export.
#include "edge_probe.h"
#include "brletrav.h"
#include "bredge.h"
#include "gecurv3d.h"
#include "geintrvl.h"
#include <cmath>
#include <ostream>
namespace {
void point(std::ostream& out, const AcGePoint3d& p) {
    if (std::isfinite(p.x) && std::isfinite(p.y) && std::isfinite(p.z))
        out << '[' << p.x << ',' << p.y << ',' << p.z << ']';
    else out << "null";
}
void vertex(std::ostream& out, const AcBrEdge& edge, bool first,
            std::vector<AcBrVertex>& identities) {
    AcBrVertex v;
    const auto status = first ? edge.getVertex1(v) : edge.getVertex2(v);
    out << "{\"status\":" << int(status);
    if (status == AcBr::eOk) {
        std::size_t id = 0;
        while (id < identities.size() && !v.isEqualTo(&identities[id])) ++id;
        if (id == identities.size()) identities.push_back(v);
        AcGePoint3d p;
        const auto queried = v.getPoint(p);
        out << ",\"loop_local_identity\":" << id
            << ",\"point_status\":" << int(queried) << ",\"point\":";
        if (queried == AcBr::eOk) point(out, p); else out << "null";
    }
    out << '}';
}
void flag(std::ostream& out, AcBr::ErrorStatus status, Adesk::Boolean value) {
    out << "{\"status\":" << int(status) << ",\"value\":";
    if (status == AcBr::eOk) out << (value ? "true" : "false"); else out << "null";
    out << '}';
}
}
void emitEdgeProbe(std::ostream& out, const AcBrLoopEdgeTraverser& traversal,
                   const AcGeCurve3d* curve, std::vector<AcBrVertex>& identities) {
    AcBrEdge edge;
    const auto opened = traversal.getEdge(edge);
    out << ",\"native_edge_probe\":{\"edge_status\":" << int(opened);
    if (opened == AcBr::eOk) {
        Adesk::Boolean toCurve = false, toLoop = false;
        const auto a = edge.getOrientToCurve(toCurve);
        const auto b = traversal.getEdgeOrientToLoop(toLoop);
        out << ",\"orient_to_curve\":"; flag(out, a, toCurve);
        out << ",\"orient_to_loop\":"; flag(out, b, toLoop);
        out << ",\"vertex1\":"; vertex(out, edge, true, identities);
        out << ",\"vertex2\":"; vertex(out, edge, false, identities);
    }
    if (curve) {
        AcGeInterval interval;
        AcGePoint3d start, end;
        curve->getInterval(interval, start, end);
        const bool bounded = interval.isBounded() && std::isfinite(interval.lowerBound())
            && std::isfinite(interval.upperBound());
        out << ",\"oriented_interval\":";
        if (bounded) {
            out << '[' << interval.lowerBound() << ',' << interval.upperBound() << ']';
            out << ",\"interval_start\":"; point(out, start);
            out << ",\"interval_end\":"; point(out, end);
            out << ",\"evaluated_start\":"; point(out, curve->evalPoint(interval.lowerBound()));
            out << ",\"evaluated_end\":"; point(out, curve->evalPoint(interval.upperBound()));
        } else out << "null";
    }
    out << '}';
}
