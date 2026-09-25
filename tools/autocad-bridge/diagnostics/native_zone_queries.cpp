#include "native_zone_queries.h"
#include "file_io.h"
#include "AcString.h"
#include "dbsymtb.h"
#include "dbsymutl.h"
#include <algorithm>
#include <cmath>
#include <map>
#include <stdexcept>

namespace ga::zone {
std::string quote(const std::string& s) { return "\"" + ga::bridge::jsonEscape(s) + "\""; }
void xyz(std::ostream& out, const AcGePoint3d& p) { out << '[' << p.x << ',' << p.y << ',' << p.z << ']'; }
AcDbCurve* openCurve(AcDbDatabase& db, const std::string& handle, ga::direct::ReadEntities& owner) {
    AcDbObjectId id;
    auto status = db.getAcDbObjectId(id, false, AcDbHandle(AcString(handle.c_str()).kwszPtr()));
    AcDbEntity* entity = nullptr;
    if (status == Acad::eOk) status = acdbOpenObject(entity, id, AcDb::kForRead);
    if (status != Acad::eOk || !entity) throw std::runtime_error("utility open status " + std::to_string(int(status)));
    owner.values.push_back(entity);
    auto* curve = AcDbCurve::cast(entity);
    if (!curve || entity->ownerId() != acdbSymUtil()->blockModelSpaceId(&db))
        throw std::runtime_error("this zone experiment requires a model-space utility curve");
    return curve;
}
CurveAnswer planarDistance(const AcDbCurve& curve, const AcGePoint3d& p) {
    CurveAnswer result;
    // AutoCAD projects for the nearest-point query, then returns a point on
    // the ORIGINAL 3D curve. Clearance is XY, not the returned 3D distance.
    result.status = int(curve.getClosestPointTo(p, AcGeVector3d::kZAxis, result.nearest, false));
    if (result.status != int(Acad::eOk)) return result;
    const auto& n = result.nearest;
    result.distance = std::hypot(p.x - n.x, p.y - n.y);
    if (!std::isfinite(n.x) || !std::isfinite(n.y) || !std::isfinite(n.z) || !std::isfinite(result.distance))
        result.status = -1;
    return result;
}
Verdict evaluate(const ga::direct::Prepared& building, const ga::direct::Prepared& site,
                 const AcDbCurve& utility, const Policy& policy, const AcGePoint3d& point) {
    Verdict v;
    // This bounded experiment is explicitly an XY plan at z=0. Prepared area
    // bounds are checked by the caller. It does not silently flatten tilted BReps.
    AcGePoint3d planar(point.x, point.y, 0);
    v.building = ga::direct::query(building, planar, false, true);
    v.site = ga::direct::query(site, planar, false, true);
    v.utility = planarDistance(utility, point);
    const auto areaKnown = [](const ga::direct::Answer& a) {
        return a.status == 0 && a.membership != "unknown" && a.distanceComplete;
    };
    // Known conflicts remain conflicts even when another observation fails.
    if (areaKnown(v.site) && v.site.membership == "outside") v.reason = "outside_site";
    else if (areaKnown(v.site) && v.site.distance < policy.siteClearance) v.reason = "site_clearance";
    else if (areaKnown(v.building) && v.building.membership != "outside") v.reason = "inside_building";
    else if (areaKnown(v.building) && v.building.distance < policy.buildingClearance) v.reason = "building_clearance";
    else if (v.utility.status == 0 && v.utility.distance < policy.utilityClearance) v.reason = "utility_clearance";
    if (!v.reason.empty()) v.result = "blocked";
    else if (!areaKnown(v.site) || !areaKnown(v.building) || v.utility.status != 0) v.reason = "native_query_unavailable";
    else { v.result = "clear_of_selected_objects_only"; v.reason = "selected_checks_passed"; }
    return v;
}
void emit(std::ostream& out, const AcGePoint3d& p, const Verdict& v) {
    out << "{\"point\":"; xyz(out, p);
    out << ",\"result\":" << quote(v.result) << ",\"reason\":" << quote(v.reason)
        << ",\"building\":{\"membership\":" << quote(v.building.membership)
        << ",\"status\":" << v.building.status << ",\"distance_complete\":" << (v.building.distanceComplete ? "true" : "false")
        << ",\"distance\":";
    if (v.building.distanceComplete) out << v.building.distance; else out << "null";
    out << "},\"site\":{\"membership\":" << quote(v.site.membership) << ",\"status\":" << v.site.status
        << ",\"distance_complete\":" << (v.site.distanceComplete ? "true" : "false") << ",\"distance\":";
    if (v.site.distanceComplete) out << v.site.distance; else out << "null";
    out << "},\"utility\":{\"status\":" << v.utility.status << ",\"distance_xy\":";
    if (!v.utility.status) out << v.utility.distance; else out << "null";
    out << ",\"nearest_original_point\":"; xyz(out, v.utility.nearest); out << "}}";
}
void inventory(AcDbDatabase& db, const AcGePoint3d& point, std::ostream& out) {
    AcDbBlockTableRecord* model = nullptr;
    if (acdbOpenObject(model, acdbSymUtil()->blockModelSpaceId(&db), AcDb::kForRead) != Acad::eOk)
        throw std::runtime_error("model space unavailable");
    struct Close { AcDbBlockTableRecord* value; ~Close() { value->close(); } } closer{model};
    AcDbBlockTableRecordIterator* raw = nullptr;
    if (model->newIterator(raw) != Acad::eOk || !raw) throw std::runtime_error("model iterator unavailable");
    std::unique_ptr<AcDbBlockTableRecordIterator> iterator(raw);
    struct Row { std::string handle, layer, type; CurveAnswer answer; AcGePoint3d start, end; };
    std::vector<Row> near;
    std::map<std::string, unsigned> layers, failures;
    unsigned scanned = 0, curves = 0;
    for (; !iterator->done(); iterator->step()) {
        if (++scanned > 250000) throw std::runtime_error("bounded inventory exceeded");
        AcDbEntity* entity = nullptr;
        if (iterator->getEntity(entity, AcDb::kForRead) != Acad::eOk || !entity)
            throw std::runtime_error("entity unavailable during inventory");
        const std::string layer = AcString(entity->layer()).utf8Str(); ++layers[layer];
        if (auto* curve = AcDbCurve::cast(entity)) {
            ++curves;
            const auto a = planarDistance(*curve, point);
            if (a.status) ++failures[layer + ":" + std::to_string(a.status)];
            else {
                AcDbHandle h; entity->getAcDbHandle(h); ACHAR htext[32] = {}; h.getIntoAsciiBuffer(htext);
                Row row{AcString(htext).utf8Str(), layer, AcString(entity->isA()->name()).utf8Str(), a, {}, {}};
                curve->getStartPoint(row.start); curve->getEndPoint(row.end);
                near.push_back(row);
            }
        }
        entity->close();
    }
    std::sort(near.begin(), near.end(), [](const Row& a, const Row& b) { return a.answer.distance < b.answer.distance; });
    out << "{\"scope\":\"root model space only; no block traversal\",\"entities\":" << scanned
        << ",\"curves\":" << curves << ",\"layers\":{";
    bool first = true;
    for (const auto& p : layers) { out << (first ? "" : ",") << quote(p.first) << ':' << p.second; first = false; }
    out << "},\"query_failures\":{"; first = true;
    for (const auto& p : failures) { out << (first ? "" : ",") << quote(p.first) << ':' << p.second; first = false; }
    out << "},\"nearest\":[";
    for (std::size_t i = 0; i < std::min<std::size_t>(40, near.size()); ++i) {
        const auto& r = near[i];
        out << (i ? "," : "") << "{\"handle\":" << quote(r.handle) << ",\"layer\":" << quote(r.layer)
            << ",\"entity_type\":" << quote(r.type) << ",\"distance_xy\":" << r.answer.distance << ",\"nearest\":";
        xyz(out, r.answer.nearest); out << ",\"start\":"; xyz(out, r.start); out << ",\"end\":"; xyz(out, r.end); out << '}';
    }
    out << "]}";
}
}
