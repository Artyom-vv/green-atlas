#include "cad_release.h"
#include "file_io.h"
#include "session_capture_internal.h"
#include "dbapserv.h"
#include "dbsymutl.h"
#include "DbField.h"
#include "acdbxref.h"
#include "aced.h"
#include "adslib.h"
#include <cmath>
#include <fstream>
#include <iomanip>
#include <set>
#include <sstream>

namespace ga::queryWorker {
namespace {
namespace fs = std::filesystem;
using ga::capture::checked;
using ga::capture::open;
using ga::capture::quote;
constexpr std::size_t kMaxPlants = 100000;
constexpr std::size_t kMaxRequestBytes = 4 * 1024 * 1024;
constexpr double kCoordinateTolerance = 1e-8;
struct Plant { std::string id, kind; double x, y, radius; };
struct WorkingScope {
    AcDbDatabase* previous = acdbHostApplicationServices()->workingDatabase();
    ~WorkingScope() { acdbHostApplicationServices()->setWorkingDatabase(previous); }
};
struct FieldsScope {
    AcDbField::EvalOption previous = acdbGlobalFieldEvaluationOption();
    bool active = true;
    FieldsScope() { checked(acdbSetGlobalFieldEvaluationOption(AcDbField::kDisable), "disable field refresh"); }
    void restore() {
        checked(acdbSetGlobalFieldEvaluationOption(previous), "restore field refresh");
        if (acdbGlobalFieldEvaluationOption() != previous) throw std::runtime_error("field refresh not restored");
        active = false;
    }
    ~FieldsScope() { if (active) acdbSetGlobalFieldEvaluationOption(previous); }
};
std::string filename(AcDbDatabase& db) {
    const ACHAR* value = nullptr;
    checked(db.getFilename(value), "database filename");
    if (!value) throw std::runtime_error("database has no filename");
    return AcString(value).utf8Str();
}
std::vector<Plant> readPlants(const fs::path& path, unsigned& units) {
    if (fs::file_size(path) > kMaxRequestBytes) throw std::runtime_error("release request too large");
    std::ifstream in(path); std::string schema, extra; std::size_t count = 0;
    if (!std::getline(in, schema) || schema != "green-atlas.cad-release/1" ||
        !(in >> units >> count) || units > 24 || count == 0 || count > kMaxPlants)
        throw std::runtime_error("invalid release request");
    std::vector<Plant> result; std::set<std::string> ids;
    for (std::size_t i = 0; i < count; ++i) {
        Plant p;
        if (!(in >> p.id >> p.kind >> p.x >> p.y >> p.radius) || p.id.size() > 80 ||
            p.id.find_first_not_of("0123456789abcdefABCDEF-_") != std::string::npos ||
            !ids.insert(p.id).second || (p.kind != "tree" && p.kind != "shrub") ||
            !std::isfinite(p.x) || !std::isfinite(p.y) || !std::isfinite(p.radius) || p.radius <= 0)
            throw std::runtime_error("invalid planting in release request");
        result.push_back(p);
    }
    if (in >> extra) throw std::runtime_error("trailing release request data");
    return result;
}
std::string layer(AcDbDatabase& db, const std::string& base, int color) {
    auto table = open<AcDbLayerTable>(db.layerTableId(), AcDb::kForWrite);
    auto name = base; unsigned suffix = 2;
    while (table->has(AcString(name.c_str()).kwszPtr())) name = base + "_" + std::to_string(suffix++);
    auto record = std::make_unique<AcDbLayerTableRecord>();
    checked(record->setName(AcString(name.c_str()).kwszPtr()), "set planting layer name");
    AcCmColor value; value.setColorIndex(color); record->setColor(value);
    AcDbObjectId id; checked(table->add(id, record.get()), "create planting layer");
    record.release()->close();
    return name;
}
std::string registerApp(AcDbDatabase& db) {
    auto table = open<AcDbRegAppTable>(db.regAppTableId(), AcDb::kForWrite);
    std::string name = "GREEN_ATLAS_RELEASE"; unsigned suffix = 2;
    while (table->has(AcString(name.c_str()).kwszPtr())) name = "GREEN_ATLAS_RELEASE_" + std::to_string(suffix++);
    auto record = std::make_unique<AcDbRegAppTableRecord>();
    checked(record->setName(AcString(name.c_str()).kwszPtr()), "release app name");
    AcDbObjectId id; checked(table->add(id, record.get()), "release app record");
    record.release()->close(); return name;
}
void load(AcDbDatabase& db, const fs::path& path) {
    checked(db.readDwgFile(AcString(path.string().c_str()).kwszPtr(),
        AcDbDatabase::kForReadAndAllShare, true), "open captured DWG");
    checked(db.closeInput(true), "close DWG input");
    acdbHostApplicationServices()->setWorkingDatabase(&db);
    checked(acdbResolveCurrentXRefs(&db, false, false), "resolve packaged references");
}
void verifyPlant(AcDbDatabase& db, const std::string& handle, const Plant& plant, const std::string& app) {
    AcDbObjectId id;
    checked(db.getAcDbObjectId(id, false, AcDbHandle(AcString(handle.c_str()).kwszPtr())), "reopen planting handle");
    auto circle = open<AcDbCircle>(id);
    if ((circle->center() - AcGePoint3d(plant.x, plant.y, 0)).length() > kCoordinateTolerance ||
        std::abs(circle->radius() - plant.radius) > kCoordinateTolerance)
        throw std::runtime_error("planting changed on save/reopen");
    resbuf* data = circle->xData(AcString(app.c_str()).kwszPtr());
    const bool matches = data && data->rbnext && data->rbnext->restype == 1000 &&
        AcString(data->rbnext->resval.rstring).utf8Str() == plant.id;
    if (data) acutRelRb(data);
    if (!matches) throw std::runtime_error("planting identity lost on reopen");
}
}
void releasePackageCommand() {
    try {
        auto* bootstrap = acdbHostApplicationServices()->workingDatabase();
        const auto root = fs::canonical(filename(*bootstrap)).parent_path();
        if (root.filename().string().rfind("ga-cad-release-", 0) != 0)
            throw std::runtime_error("private release job required");
        const auto request = root / "request.txt", source = root / "package/host.dwg";
        const auto target = root / "result/planting-plan.dwg";
        if (fs::exists(target) || fs::exists(root / "release.json")) throw std::runtime_error("release output exists");
        const auto requestHash = ga::bridge::sha256File(request.string());
        const auto sourceHash = ga::bridge::sha256File(source.string());
        unsigned units = 0; const auto plants = readPlants(request, units);
        std::vector<std::string> handles; std::vector<ga::capture::Instance> before, after;
        std::string trees, shrubs, app;
        {
            FieldsScope fields;
            {
                AcDbDatabase db(false, true); WorkingScope context;
                load(db, source);
                if (unsigned(db.insunits()) != units) throw std::runtime_error("release units mismatch");
                before = ga::capture::captureInstances(db);
                trees = layer(db, "GREEN_ATLAS_TREES", 94);
                shrubs = layer(db, "GREEN_ATLAS_SHRUBS", 83);
                app = registerApp(db);
                auto model = open<AcDbBlockTableRecord>(acdbSymUtil()->blockModelSpaceId(&db), AcDb::kForWrite);
                for (const auto& plant : plants) {
                    if (acedUsrBrk()) throw std::runtime_error("release cancelled");
                    auto circle = std::make_unique<AcDbCircle>(AcGePoint3d(plant.x, plant.y, 0), AcGeVector3d::kZAxis, plant.radius);
                    circle->setDatabaseDefaults(&db);
                    checked(circle->setLayer(AcString((plant.kind == "tree" ? trees : shrubs).c_str()).kwszPtr()), "assign planting layer");
                    AcDbObjectId id; checked(model->appendAcDbEntity(id, circle.get()), "append planting");
                    auto entity = ga::capture::Open<AcDbCircle>(circle.release());
                    resbuf* data = acutBuildList(1001, AcString(app.c_str()).kwszPtr(),
                        1000, AcString(plant.id.c_str()).kwszPtr(), 1000, AcString(plant.kind.c_str()).kwszPtr(), 0);
                    const auto status = entity->setXData(data); acutRelRb(data);
                    checked(status, "planting identity"); handles.push_back(ga::capture::handle(id));
                }
                model.reset();
                checked(db.saveAs(AcString(target.string().c_str()).kwszPtr(), false, AcDb::kDHL_CURRENT), "save planting result");
            }
            {
                AcDbDatabase db(false, true); WorkingScope context; load(db, target);
                if (unsigned(db.insunits()) != units) throw std::runtime_error("reopened units mismatch");
                after = ga::capture::captureInstances(db);
                for (std::size_t i = 0; i < plants.size(); ++i) verifyPlant(db, handles[i], plants[i], app);
                if (after.size() != before.size() + plants.size()) throw std::runtime_error("reopened source instance count differs");
                std::map<std::string, const ga::capture::Instance*> found;
                for (const auto& item : after) found.emplace(item.route, &item);
                for (const auto& item : before) {
                    const auto match = found.find(item.route);
                    if (match == found.end() || match->second->type != item.type || match->second->layer != item.layer ||
                        match->second->unavailable != item.unavailable || !match->second->transform.isEqualTo(item.transform))
                        throw std::runtime_error("source instance changed on result reopen");
                }
            }
            fields.restore();
        }
        if (sourceHash != ga::bridge::sha256File(source.string()) || requestHash != ga::bridge::sha256File(request.string()) ||
            acdbHostApplicationServices()->workingDatabase() != bootstrap)
            throw std::runtime_error("release input/context changed");
        std::ostringstream out; out << std::setprecision(17)
            << "{\"schema\":\"green-atlas.cad-release/1\",\"request_sha256\":" << quote(requestHash)
            << ",\"source_sha256\":" << quote(sourceHash) << ",\"result_sha256\":"
            << quote(ga::bridge::sha256File(target.string())) << ",\"source_instances\":" << before.size()
            << ",\"result_instances\":" << after.size() << ",\"units_code\":" << units
            << ",\"reopened\":true,\"plantings\":[";
        for (std::size_t i = 0; i < plants.size(); ++i) {
            const auto& plant = plants[i];
            out << (i ? "," : "") << "{\"id\":" << quote(plant.id) << ",\"handle\":" << quote(handles[i])
                << ",\"kind\":" << quote(plant.kind) << ",\"x\":" << plant.x << ",\"y\":" << plant.y
                << ",\"radius\":" << plant.radius << '}';
        }
        out << "]}";
        if (!ga::bridge::writeAtomicText((root / "release.json").string(), out.str())) throw std::runtime_error("release publication failed");
    } catch (const std::exception& error) {
        acutPrintf(L"\nGreen Atlas release failed: %s", AcString(error.what()).kwszPtr());
    }
}
}
