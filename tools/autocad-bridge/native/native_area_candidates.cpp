#include "native_area_candidates.h"
#include "native_area_group.h"
#include "AcString.h"
#include "dbcurve.h"
#include <algorithm>
#include <cmath>
#include <numeric>
#include <set>
#include <stdexcept>

namespace ga::nativeQuery {
namespace {
bool finite(const AcGePoint3d& point) {
    return std::isfinite(point.x) && std::isfinite(point.y) && std::isfinite(point.z);
}
struct Components {
    std::vector<std::size_t> parents;
    explicit Components(std::size_t count): parents(count) {
        std::iota(parents.begin(), parents.end(), 0);
    }
    std::size_t root(std::size_t index) {
        while (index != parents[index]) {
            parents[index] = parents[parents[index]];
            index = parents[index];
        }
        return index;
    }
    void join(std::size_t a, std::size_t b) { parents[root(a)] = root(b); }
};
}
void AreaCandidates::consider(AcDbEntity& entity, const std::string& route) {
    auto* curve = AcDbCurve::cast(&entity);
    if (!curve) return;
    const std::string type = AcString(entity.isA()->name()).utf8Str();
    if (type != "AcDbLine" && type != "AcDbArc" && type != "AcDbPolyline"
        && type != "AcDbCircle" && type != "AcDbEllipse" && type != "AcDbSpline") return;
    ++observed;
    AcString layer;
    entity.layer(layer);
    Curve row{route, {}};
    if (entity.isWriteEnabled() || curve->getStartPoint(row.ends[0]) != Acad::eOk
        || curve->getEndPoint(row.ends[1]) != Acad::eOk
        || !finite(row.ends[0]) || !finite(row.ends[1])) {
        unavailable.push_back({{route}, layer.utf8Str(), "native endpoints unavailable or entity write-open"});
        return;
    }
    if (curve->isClosed() || row.ends[0].distanceTo(row.ends[1]) <= kNativeJoinTolerance) {
        ++individuallyClosed; // Independently checked by the single-object native query.
        return;
    }
    const auto slash = route.find_last_of('/');
    Scope scope{slash == std::string::npos ? "" : route.substr(0, slash), layer.utf8Str()};
    scopes[scope].push_back(std::move(row));
}

std::vector<AreaCandidate> AreaCandidates::collect() const {
    std::vector<AreaCandidate> result = unavailable;
    for (const auto& [scope, curves] : scopes) {
        Components components(curves.size());
        std::vector<std::array<unsigned, 2>> degrees(curves.size(), {0, 0});
        // Sweep native endpoints by X, then test full XYZ numerical equality.
        // This avoids an all-pairs scan across an entire street and does not
        // quantize coordinates or change topology at a bucket boundary.
        struct End { AcGePoint3d point; std::size_t curve, end; };
        std::vector<End> ends;
        ends.reserve(curves.size() * 2);
        for (std::size_t i = 0; i < curves.size(); ++i)
            for (std::size_t e = 0; e < 2; ++e) ends.push_back({curves[i].ends[e], i, e});
        std::sort(ends.begin(), ends.end(), [](const auto& a, const auto& b) { return a.point.x < b.point.x; });
        for (std::size_t i = 0; i < ends.size(); ++i) {
            for (std::size_t j = i + 1; j < ends.size()
                 && ends[j].point.x - ends[i].point.x <= kNativeJoinTolerance; ++j) {
                const auto& a = ends[i]; const auto& b = ends[j];
                if (a.curve != b.curve && a.point.distanceTo(b.point) <= kNativeJoinTolerance) {
                    ++degrees[a.curve][a.end]; ++degrees[b.curve][b.end];
                    components.join(a.curve, b.curve);
                }
            }
        }
        std::map<std::size_t, std::vector<std::size_t>> groups;
        for (std::size_t i = 0; i < curves.size(); ++i) groups[components.root(i)].push_back(i);
        for (const auto& [_, members] : groups) {
            AreaCandidate candidate;
            candidate.layer = scope.second;
            bool open = false, ambiguous = false;
            for (auto index : members) {
                candidate.routes.push_back(curves[index].route);
                for (auto degree : degrees[index]) {
                    open = open || degree == 0;
                    ambiguous = ambiguous || degree > 1;
                }
            }
            std::sort(candidate.routes.begin(), candidate.routes.end());
            if (ambiguous) candidate.error = "ambiguous native endpoint junction";
            else if (open) candidate.error = "open native endpoint chain";
            else if (members.size() > kAreaGroupMemberLimit) candidate.error = "native area group member limit";
            result.push_back(std::move(candidate));
        }
    }
    std::sort(result.begin(), result.end(), [](const auto& a, const auto& b) { return a.routes < b.routes; });
    return result;
}
}
