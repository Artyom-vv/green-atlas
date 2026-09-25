// Bounded native calculation experiment, never a product import or CAD writer.
#include "native_zone_queries.h"
#include "file_io.h"
#include "rxregsvc.h"
#include "core_rxmfcapi.h"
#include "aced.h"
#include "adslib.h"
#include "AcString.h"
#include "geblok3d.h"
#include "dbents.h"
#ifdef GA_XREF_ZONE
#include "xref_instance_access.h"
#endif
#include <chrono>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <map>
#include <sstream>
#include <stdexcept>

namespace {
using namespace ga::zone;
using Clock = std::chrono::steady_clock;
std::string line(std::istream& in) {
    std::string s;
    if (!std::getline(in, s)) throw std::runtime_error("truncated request");
    if (!s.empty() && s.back() == '\r') s.pop_back();
    return s;
}
double elapsed(Clock::time_point start) { return std::chrono::duration<double, std::milli>(Clock::now() - start).count(); }
struct Control { AcGePoint3d point; std::string expected, reason, label; };
void requirePlanarXY(const ga::direct::Prepared& p) {
    AcGeBoundBlock3d bound; AcGePoint3d a, b;
    if (p.world.getBoundBlock(bound) != AcBr::eOk) throw std::runtime_error("area bounds unavailable");
    bound.getMinMaxPoints(a, b);
    if (std::abs(a.z) > 1e-7 || std::abs(b.z) > 1e-7)
        throw std::runtime_error("area is not in the experiment XY plane; no silent flattening");
}
void run() {
    ACHAR path[4096] = {};
    if (acedGetString(1, _T("\nNative zone request: "), path) != RTNORM) return;
    try {
        std::ifstream in(AcString(path).utf8Str());
        const std::string source = line(in), networkSource = line(in), output = line(in), mode = line(in);
        const std::string buildingHandle = line(in), siteHandle = line(in), utilityHandle = line(in);
        double xmin, ymin, xmax, ymax, step;
        std::istringstream box(line(in));
        if (!(box >> xmin >> ymin >> xmax >> ymax >> step) || !(step >= 0.1)
            || !std::isfinite(step) || !std::isfinite(xmin) || !std::isfinite(ymin)
            || !std::isfinite(xmax) || !std::isfinite(ymax) || !(xmax > xmin) || !(ymax > ymin)
            || (std::floor((xmax-xmin)/step)+1)*(std::floor((ymax-ymin)/step)+1) > 10000)
            throw std::runtime_error("invalid bounded query window");
        Policy policy; int repeats;
        std::istringstream rules(line(in));
        if (!(rules >> policy.buildingClearance >> policy.utilityClearance >> policy.siteClearance >> policy.spacing >> repeats)
            || repeats < 1 || repeats > 5 || policy.spacing <= 0 || policy.buildingClearance < 0
            || policy.utilityClearance < 0 || policy.siteClearance < 0
            || !std::isfinite(policy.spacing) || !std::isfinite(policy.buildingClearance)
            || !std::isfinite(policy.utilityClearance) || !std::isfinite(policy.siteClearance))
            throw std::runtime_error("invalid diagnostic policy");
        const int count = std::stoi(line(in));
        if (count < 0 || count > 64) throw std::runtime_error("invalid controls count");
        std::vector<Control> controls;
        for (int i = 0; i < count; ++i) {
            Control c; std::istringstream row(line(in));
            if (!(row >> c.point.x >> c.point.y >> c.point.z >> c.expected >> c.reason >> c.label)
                || !std::isfinite(c.point.x) || !std::isfinite(c.point.y) || !std::isfinite(c.point.z))
                throw std::runtime_error("invalid control row");
            controls.push_back(c);
        }
        if (std::ifstream(output).good()) throw std::runtime_error("output already exists");
        const auto sourceHash = ga::bridge::sha256File(source), networkHash = ga::bridge::sha256File(networkSource);
        if (sourceHash.empty() || networkHash.empty()) throw std::runtime_error("source hash unavailable");
        auto* services = acdbHostApplicationServices();
#ifdef GA_XREF_ZONE
        AcDbDatabase& db=*services->workingDatabase();
        AcDbDatabase& networks=db;
        const ACHAR* actualFile=nullptr;
        if(db.getFilename(actualFile)!=Acad::eOk || source!=AcString(actualFile).utf8Str())
            throw std::runtime_error("request differs from the actual loaded host");
#else
        AcDbDatabase db(false, true), networks(false, true);
#endif
        struct Restore { AcDbHostApplicationServices* s; AcDbDatabase* prior; ~Restore() { s->setWorkingDatabase(prior); } }
            restore{services, services->workingDatabase()};
        const auto loaded = Clock::now();
#ifndef GA_XREF_ZONE
        const auto ds = db.dxfIn(AcString(source.c_str()).kwszPtr());
        const auto ns = networks.dxfIn(AcString(networkSource.c_str()).kwszPtr());
        if (ds != Acad::eOk || ns != Acad::eOk) throw std::runtime_error("DXF open statuses " + std::to_string(int(ds)) + "," + std::to_string(int(ns)));
#endif
        if (int(db.insunits()) != 6 || int(networks.insunits()) != 6)
            throw std::runtime_error("this experiment requires both drawings in metres");
        const double loadMs = elapsed(loaded);
        services->setWorkingDatabase(&db);
        std::ostringstream out; out << std::setprecision(17);
        out << "{\"schema\":\"green-atlas.native-zone/1\",\"mode\":" << quote(mode)
            << ",\"source_sha256\":" << quote(sourceHash) << ",\"network_sha256\":" << quote(networkHash)
            << ",\"source\":" << quote(source) << ",\"network_source\":" << quote(networkSource)
            << ",\"load_ms\":" << loadMs;
        if (mode == "inventory") {
            services->setWorkingDatabase(&networks);
            out << ",\"inventory\":";
            inventory(networks, {(xmin+xmax)/2, (ymin+ymax)/2, 0}, out);
        } else if (mode == "evaluate") {
            const auto preparedAt = Clock::now();
#ifdef GA_XREF_ZONE
            ga::xref::Instance buildingInstance, siteInstance, utilityInstance;
#endif
            ga::direct::Prepared building, site;
#ifdef GA_XREF_ZONE
            ga::xref::resolve(db,buildingHandle,buildingInstance);
            ga::xref::resolve(db,siteHandle,siteInstance);
            ga::xref::resolve(db,utilityHandle,utilityInstance);
            ga::direct::prepareResolved(buildingInstance.entity,buildingInstance.parents,buildingInstance.transform,building);
            ga::direct::prepareResolved(siteInstance.entity,siteInstance.parents,siteInstance.transform,site);
            // This bounded comparison targets a native LINE. No polyline clone
            // repair and no private reinterpretation of an unresolved XREF.
            if(!AcDbLine::cast(utilityInstance.entity)) throw std::runtime_error("XREF zone expects native LINE");
            AcDbEntity* rawUtility=nullptr;
            const auto transformed=utilityInstance.entity->getTransformedCopy(utilityInstance.transform,rawUtility);
            std::unique_ptr<AcDbEntity> worldUtility(rawUtility);
            auto* utility=AcDbCurve::cast(rawUtility);
            if(transformed!=Acad::eOk || !utility) throw std::runtime_error("native utility transform failed");
#else
            ga::direct::prepare(db, buildingHandle, {}, building);
            ga::direct::prepare(db, siteHandle, {}, site);
            services->setWorkingDatabase(&networks);
            ga::direct::ReadEntities owner;
            auto* utility = openCurve(networks, utilityHandle, owner);
#endif
            requirePlanarXY(building); requirePlanarXY(site);
#ifdef GA_XREF_ZONE
            out << ",\"transform_provider\":\"AcDbCompoundObjectId.getTransform\",\"compound_matrix_deltas\":["
                << buildingInstance.compoundMatrixDelta << ',' << siteInstance.compoundMatrixDelta
                << ',' << utilityInstance.compoundMatrixDelta << ']';
#endif
            out << ",\"prepare_ms\":" << elapsed(preparedAt)
                << ",\"building\":{\"handle\":" << quote(buildingHandle) << ",\"layer\":" << quote(building.layer) << "}"
                << ",\"site\":{\"handle\":" << quote(siteHandle) << ",\"layer\":" << quote(site.layer) << "}"
                << ",\"utility\":{\"handle\":" << quote(utilityHandle) << ",\"layer\":" << quote(AcString(utility->layer()).utf8Str())
                << ",\"class\":" << quote(AcString(utility->isA()->name()).utf8Str()) << "}"
                << ",\"policy\":{\"scope\":\"selected objects only, not legal acceptance\",\"building_clearance_m\":" << policy.buildingClearance
                << ",\"utility_axis_clearance_m_experimental\":" << policy.utilityClearance << ",\"site_clearance_m\":" << policy.siteClearance
                << ",\"spacing_m\":" << policy.spacing << "}";
            std::vector<AcGePoint3d> points;
            const int nx = int(std::floor((xmax-xmin)/step)), ny = int(std::floor((ymax-ymin)/step));
            for (int x = 0; x <= nx; ++x) for (int y = 0; y <= ny; ++y) points.emplace_back(xmin+x*step, ymin+y*step, 0);
            std::vector<Verdict> first; std::vector<double> times;
            unsigned mismatches = 0;
            for (int repeat = 0; repeat < repeats; ++repeat) {
                const auto started = Clock::now();
                for (std::size_t i = 0; i < points.size(); ++i) {
                    const auto v = evaluate(building, site, *utility, policy, points[i]);
                    if (!repeat) first.push_back(v);
                    else {
                        double delta = 0;
                        if (v.result != first[i].result || v.reason != first[i].reason
                            || !ga::direct::stable(v.building, first[i].building, delta)
                            || !ga::direct::stable(v.site, first[i].site, delta)
                            || v.utility.status != first[i].utility.status
                            || (!v.utility.status && std::abs(v.utility.distance-first[i].utility.distance)>1e-8)) ++mismatches;
                    }
                }
                times.push_back(elapsed(started));
            }
            // Deterministic experimental candidate selection. Both manual and
            // automatic queries call evaluate; no product state is published.
            std::vector<AcGePoint3d> selected;
            for (std::size_t i = 0; i < points.size() && selected.size() < 81; ++i) {
                if (first[i].result != "clear_of_selected_objects_only") continue;
                bool spaced = true;
                for (const auto& p : selected) if (p.distanceTo(points[i]) < policy.spacing) spaced = false;
                if (spaced) selected.push_back(points[i]);
            }
            unsigned manualMismatch = 0;
            for (const auto& p : selected) if (evaluate(building, site, *utility, policy, p).result != "clear_of_selected_objects_only") ++manualMismatch;
            std::map<std::string, unsigned> reasons;
            unsigned unavailableObservations = 0;
            out << ",\"query_count\":" << points.size() << ",\"repeats\":" << repeats << ",\"batch_ms\":[";
            for (std::size_t i=0; i<times.size(); ++i) out << (i ? "," : "") << times[i];
            out << "],\"repeat_mismatches\":" << mismatches << ",\"manual_recheck_mismatches\":" << manualMismatch << ",\"answers\":[";
            for (std::size_t i=0; i<points.size(); ++i) {
                if (i) out << ',';
                const auto& v=first[i]; emit(out, points[i], v); ++reasons[v.reason];
                if (v.building.status || v.building.membership=="unknown" || !v.building.distanceComplete) ++unavailableObservations;
                if (v.site.status || v.site.membership=="unknown" || !v.site.distanceComplete) ++unavailableObservations;
                if (v.utility.status) ++unavailableObservations;
            }
            out << "],\"reason_counts\":{"; bool firstReason=true;
            for (const auto& p:reasons) { out << (firstReason ? "" : ",") << quote(p.first) << ':' << p.second; firstReason=false; }
            out << "},\"selected\":[";
            for (std::size_t i=0; i<selected.size(); ++i) { if (i) out << ','; xyz(out, selected[i]); }
            out << "],\"controls\":["; unsigned passed=0, failed=0;
            for (std::size_t i=0; i<controls.size(); ++i) {
                const auto& c=controls[i]; const auto v=evaluate(building, site, *utility, policy, c.point);
                const bool ok=v.result==c.expected && v.reason==c.reason;
                if (ok) ++passed; else ++failed;
                out << (i ? "," : "") << "{\"label\":" << quote(c.label) << ",\"expected\":" << quote(c.expected)
                    << ",\"expected_reason\":" << quote(c.reason) << ",\"passed\":" << (ok ? "true" : "false") << ",\"actual\":";
                emit(out,c.point,v); out << '}';
            }
            // A z change must not change planar clearance. This is a deliberate
            // metamorphic query, not an assertion about the network's real depth.
            unsigned heightMismatches=0;
            for (std::size_t i=0; i<points.size(); i+=17) {
                auto lifted=points[i]; lifted.z=150;
                const auto v=evaluate(building,site,*utility,policy,lifted);
                if (v.result!=first[i].result || v.reason!=first[i].reason || v.utility.status!=first[i].utility.status
                    || (!v.utility.status && std::abs(v.utility.distance-first[i].utility.distance)>1e-8)) ++heightMismatches;
            }
            // Move a detached native LINE, not database geometry. This checks
            // XY clearance when the object itself is elevated, not just the query.
            unsigned elevatedCount=0, elevatedMismatch=0;
            int elevatedStatus=-1;
            if (AcDbLine::cast(utility)) {
                AcGeMatrix3d shift; shift.setToTranslation({0,0,150});
                AcDbEntity* raw=nullptr;
                elevatedStatus=int(utility->getTransformedCopy(shift,raw));
                std::unique_ptr<AcDbEntity> elevated(raw);
                auto* curve=AcDbCurve::cast(raw);
                if (!elevatedStatus && curve) {
                    for (std::size_t i=0; i<points.size(); i+=17) {
                        ++elevatedCount;
                        const auto a=planarDistance(*curve,points[i]);
                        if (a.status || std::abs(a.distance-first[i].utility.distance)>1e-8
                            || std::abs(a.nearest.z-first[i].utility.nearest.z-150)>1e-8) ++elevatedMismatch;
                    }
                } else if (!elevatedStatus) elevatedStatus=-1;
            }
            out << "],\"controls_passed\":" << passed << ",\"controls_failed\":" << failed
                << ",\"height_query_count\":" << (points.size()+16)/17
                << ",\"height_query_mismatches\":" << heightMismatches
                << ",\"unavailable_observations\":" << unavailableObservations
                << ",\"native_line_elevation_check\":{\"translation_z_m\":150,\"status\":" << elevatedStatus
                << ",\"queries\":" << elevatedCount << ",\"mismatches\":" << elevatedMismatch << '}';
        } else throw std::runtime_error("unknown experiment mode");
        out << ",\"sources_unchanged\":" << ((sourceHash==ga::bridge::sha256File(source) && networkHash==ga::bridge::sha256File(networkSource)) ? "true" : "false") << '}';
        if (!ga::bridge::writeAtomicText(output,out.str())) throw std::runtime_error("report write failed");
        acutPrintf(_T("\nNative zone report written"));
    } catch (const std::exception& e) { acutPrintf(_T("\nNative zone failed: %s"),AcString(e.what()).kwszPtr()); }
}
}
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode message, void* appId) {
    if (message==AcRx::kInitAppMsg) {
        acrxLoadModule(_T("AcGeomentObj.dbx"),0); acrxDynamicLinker->loadModule(_T("AcBr.dbx"),1);
        acrxDynamicLinker->unlockApplication(appId); acrxDynamicLinker->registerAppMDIAware(appId);
        acedRegCmds->addCommand(_T("GA_ZONE_DIAGNOSTIC"),_T("GAZONEFILE"),_T("GAZONEFILE"),ACRX_CMD_MODAL,run);
    } else if (message==AcRx::kUnloadAppMsg) acedRegCmds->removeGroup(_T("GA_ZONE_DIAGNOSTIC"));
    return AcRx::kRetOK;
}
