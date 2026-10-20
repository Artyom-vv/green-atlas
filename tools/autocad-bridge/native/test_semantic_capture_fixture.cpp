// Контрольный CAD в отдельном профиле Core: захват, перечень и архив вызываются
// из настоящего производственного плагина через dlopen, без рабочих проектов.
#include "capture_commands.h"
#include "file_io.h"
#include "AcString.h"
#include "aced.h"
#include "adslib.h"
#include "rxregsvc.h"
#include "core_rxmfcapi.h"
#include "dbapserv.h"
#include "dbsymutl.h"
#include "dbents.h"
#include "dbmtext.h"
#include "dbtable.h"
#include "dbhatch.h"
#include "acdbxref.h"
#include <cstdlib>
#include <dlfcn.h>
#include <filesystem>
#include <memory>
#include <stdexcept>
#include <string>

namespace {
namespace fs = std::filesystem;
void checked(Acad::ErrorStatus status, const char* action) {
    if (status != Acad::eOk) throw std::runtime_error(std::string(action) + ":" + std::to_string(int(status)));
}
template<class T> struct Close { void operator()(T* p) const { if (p) p->close(); } };
template<class T> using Open = std::unique_ptr<T, Close<T>>;
template<class T> Open<T> open(AcDbObjectId id, AcDb::OpenMode mode = AcDb::kForRead) {
    T* result = nullptr;
    checked(acdbOpenObject(result, id, mode), "fixture open");
    return Open<T>(result);
}
AcDbObjectId append(AcDbObjectId record, AcDbEntity* raw) {
    std::unique_ptr<AcDbEntity> pending(raw);
    auto owner = open<AcDbBlockTableRecord>(record, AcDb::kForWrite);
    AcDbObjectId id;
    checked(owner->appendAcDbEntity(id, pending.get()), "fixture append");
    pending.release()->close();
    return id;
}
AcDbObjectId block(AcDbDatabase& db, const wchar_t* name) {
    auto table = open<AcDbBlockTable>(db.blockTableId(), AcDb::kForWrite);
    auto pending = std::make_unique<AcDbBlockTableRecord>();
    checked(pending->setName(name), "fixture block name");
    AcDbObjectId id;
    checked(table->add(id, pending.get()), "fixture block");
    pending.release()->close(); return id;
}
void text(AcDbDatabase& db, AcDbObjectId record, const wchar_t* value, AcGePoint3d point) {
    auto pending = std::make_unique<AcDbText>();
    pending->setDatabaseDefaults(&db);
    checked(pending->setPosition(point), "fixture text anchor");
    checked(pending->setHeight(0.1), "fixture text height");
    checked(pending->setTextString(value), "fixture text value");
    append(record, pending.release());
}
AcDbObjectId attach(AcDbDatabase& db, const fs::path& file, const wchar_t* name, AcGePoint3d point) {
    AcDbObjectId id;
    checked(acdbAttachXref(&db, AcString(file.string().c_str()).kwszPtr(), name, id), "fixture XREF");
    append(acdbSymUtil()->blockModelSpaceId(&db), new AcDbBlockReference(point, id));
    return id;
}
void save(AcDbDatabase& db, const fs::path& path) {
    checked(db.saveAs(AcString(path.string().c_str()).kwszPtr(), false, AcDb::kDHL_CURRENT), "fixture save");
}
void annotationFixture(AcDbDatabase& db, AcDbObjectId model) {
    // Одна и та же CAD-дефиниция снаружи таблицы и в её ячейке:
    // принадлежность оформлению определяет расчётную роль, а не тип кривой.
    const auto child = block(db, L"ANNOTATION_CHILD");
    append(child, new AcDbLine({0, 0, 0}, {2, 0, 0}));
    append(child, new AcDbPoint({1, 1, 0}));
    auto rectangle = std::make_unique<AcDbPolyline>(); rectangle->setDatabaseDefaults(&db);
    for (const auto point : {AcGePoint2d(0, 0), AcGePoint2d(2, 0), AcGePoint2d(2, 2), AcGePoint2d(0, 2)})
        checked(rectangle->addVertexAt(rectangle->numVerts(), point), "fixture annotation vertex");
    rectangle->setClosed(true); append(child, rectangle.release());
    text(db, child, L"TABLE_NOTE", {1, 1, 0});
    auto definition = std::make_unique<AcDbAttributeDefinition>(); definition->setDatabaseDefaults(&db);
    checked(definition->setPosition({1, 1, 0}), "fixture annotation default anchor");
    checked(definition->setHeight(0.1), "fixture annotation default height");
    checked(definition->setTag(L"NOTE"), "fixture annotation default tag");
    checked(definition->setTextString(L"TABLE_DEFAULT"), "fixture annotation default value");
    append(child, definition.release());
    auto outside = std::make_unique<AcDbBlockReference>(AcGePoint3d(40, 50, 0), child);
    outside->setDatabaseDefaults(&db); checked(outside->setLayer(L"DESIGN"), "fixture annotation outside layer");
    append(model, outside.release());
    auto table = std::make_unique<AcDbTable>(); table->setDatabaseDefaults(&db);
    checked(table->setSize(2, 2), "fixture table size");
    checked(table->setRowHeight(4), "fixture table height");
    checked(table->setColumnWidth(4), "fixture table width");
    checked(table->setLayer(L"DESIGN"), "fixture table layer");
    checked(table->setBlockTableRecordId(1, 0, 0, child, false), "fixture table nested block");
    checked(table->setTextString(1, 1, 0, L"TABLE_CELL"), "fixture table cell text");
    const auto id = append(model, table.release());
    auto persisted = open<AcDbTable>(id, AcDb::kForWrite);
    checked(persisted->recomputeTableBlock(), "fixture table native layout");
}
void seed(const fs::path& root) {
    const auto source = root / "source";
    if (!fs::create_directory(source)) throw std::runtime_error("fresh fixture source required");
    {
        AcDbDatabase leaf(true, true); leaf.setInsunits(AcDb::kUnitsMeters);
        auto value = std::make_unique<AcDbMText>(); value->setDatabaseDefaults(&leaf);
        checked(value->setLocation({1, 2, 0}), "fixture mtext anchor");
        value->setContents(L"ГАЗОН");
        append(acdbSymUtil()->blockModelSpaceId(&leaf), value.release());
        save(leaf, source / "leaf.dwg");
    }
    {
        AcDbDatabase child(true, true); child.setInsunits(AcDb::kUnitsMeters);
        text(child, acdbSymUtil()->blockModelSpaceId(&child), L"d=400ст.", {2, 3, 0});
        attach(child, source / "leaf.dwg", L"LEAF", {20, 30, 0});
        save(child, source / "child.dwg");
    }
    AcDbDatabase host(true, true); host.setInsunits(AcDb::kUnitsMeters);
    const auto model = acdbSymUtil()->blockModelSpaceId(&host);
    {
        auto layers = open<AcDbLayerTable>(host.layerTableId(), AcDb::kForWrite);
        for (const auto* name : {L"DESIGN", L"SITE", L"WATER", L"L009"}) {
            auto pending = std::make_unique<AcDbLayerTableRecord>(); pending->setName(name);
            AcDbObjectId id; checked(layers->add(id, pending.get()), "fixture layer");
            pending.release()->close();
        }
    }
    auto site = std::make_unique<AcDbPolyline>(); site->setDatabaseDefaults(&host);
    for (const auto point : {AcGePoint2d(0, 0), AcGePoint2d(12, 0), AcGePoint2d(12, 12), AcGePoint2d(0, 12)})
        checked(site->addVertexAt(site->numVerts(), point), "fixture site vertex");
    site->setClosed(true);
    if (!site->isClosed()) throw std::runtime_error("fixture site closure failed");
    checked(site->setLayer(L"SITE"), "fixture site layer");
    append(model, site.release());
    auto line = std::make_unique<AcDbLine>(AcGePoint3d(0, 10, 0), AcGePoint3d(12, 10, 0));
    line->setDatabaseDefaults(&host);
    checked(line->setLayer(L"WATER"), "fixture water layer");
    append(model, line.release());
    auto lawn = std::make_unique<AcDbHatch>(); lawn->setDatabaseDefaults(&host);
    checked(lawn->setLayer(L"L009"), "fixture lawn layer");
    checked(lawn->setPattern(AcDbHatch::kPreDefined, L"SOLID"), "fixture lawn pattern");
    AcGePoint2dArray lawnVertices;
    for (const auto point : {AcGePoint2d(1, 1), AcGePoint2d(6, 1), AcGePoint2d(6, 8), AcGePoint2d(1, 8)})
        lawnVertices.append(point);
    AcGeDoubleArray lawnBulges;
    checked(lawn->appendLoop(AcDbHatch::kExternal | AcDbHatch::kPolyline, lawnVertices, lawnBulges), "fixture lawn loop");
    append(model, lawn.release());
    auto lawnLabel = std::make_unique<AcDbText>(); lawnLabel->setDatabaseDefaults(&host);
    checked(lawnLabel->setPosition({3, 4, 0}), "fixture lawn annotation anchor");
    checked(lawnLabel->setHeight(0.1), "fixture lawn annotation height");
    checked(lawnLabel->setLayer(L"L009"), "fixture lawn annotation layer");
    checked(lawnLabel->setTextString(L"ГАЗОН"), "fixture lawn annotation text");
    append(model, lawnLabel.release());
    annotationFixture(host, model);
    const auto inner = block(host, L"SEMANTIC_INNER");
    text(host, inner, L"INNER_LABEL", {1, 2, 0});
    auto definition = std::make_unique<AcDbAttributeDefinition>(); definition->setDatabaseDefaults(&host);
    checked(definition->setPosition({3, 4, 0}), "fixture default anchor");
    checked(definition->setHeight(0.1), "fixture default height");
    checked(definition->setTag(L"DIAMETER"), "fixture default tag");
    checked(definition->setTextString(L"d=999"), "fixture default value");
    append(inner, definition.release());
    const auto unused = block(host, L"SEMANTIC_UNUSED");
    text(host, unused, L"SOURCE_ONLY_UNUSED", {0, 0, 0});
    const auto outer = block(host, L"SEMANTIC_OUTER");
    const auto innerReferenceId = append(outer, new AcDbBlockReference({10, 20, 0}, inner));
    {
        auto reference = open<AcDbBlockReference>(innerReferenceId, AcDb::kForWrite);
        checked(reference->setRotation(0.15), "fixture inner rotation");
        auto attribute = std::make_unique<AcDbAttribute>(); attribute->setDatabaseDefaults(&host);
        checked(attribute->setPosition({0.25, 0.5, 0}), "fixture instance anchor");
        checked(attribute->setHeight(0.1), "fixture instance height");
        checked(attribute->setTag(L"DIAMETER"), "fixture instance tag");
        checked(attribute->setTextString(L"d=250"), "fixture instance value");
        checked(attribute->setAttributeFromBlock(reference->blockTransform()), "fixture attribute owner transform");
        AcDbObjectId id; checked(reference->appendAttribute(id, attribute.get()), "fixture attached attribute");
        attribute.release()->close();
    }
    for (int index = 0; index < 2; ++index) {
        auto reference = std::make_unique<AcDbBlockReference>(
            index ? AcGePoint3d(30, 40, 0) : AcGePoint3d(3, 3, 0), outer);
        reference->setDatabaseDefaults(&host);
        checked(reference->setLayer(L"DESIGN"), "fixture inherited layer");
        checked(reference->setRotation(index ? -0.2 : 0.3), "fixture outer rotation");
        checked(reference->setScaleFactors(index ? AcGeScale3d(1.2, 1.2, 1) : AcGeScale3d(0.8, 0.8, 1)), "fixture outer scale");
        append(model, reference.release());
    }
    const auto child = attach(host, source / "child.dwg", L"CHILD", {100, 200, 0});
    auto repeated = std::make_unique<AcDbBlockReference>(AcGePoint3d(-50, 60, 0), child);
    checked(repeated->setRotation(0.35), "fixture repeated XREF rotation");
    checked(repeated->setScaleFactors({2, 0.5, 1}), "fixture repeated XREF scale");
    append(model, repeated.release());
    const auto* overflowMode = std::getenv("GA_SEMANTIC_OVERFLOW");
    if (overflowMode && std::string(overflowMode) == "1") {
        const std::wstring overflow(5000, L'x');
        text(host, model, overflow.c_str(), {0, 0, 0});
    }
    save(host, source / "host.dwg");
}
void capture(const fs::path& root) {
    const auto* libraryName = std::getenv("GA_SEMANTIC_LIBRARY");
    void* library = libraryName ? dlopen(libraryName, RTLD_NOW | RTLD_LOCAL) : nullptr;
    if (!library) throw std::runtime_error("built production library unavailable");
    using Capture = bool(*)(const std::string&, std::string&, const ga::bridge::CaptureObserver&);
    using Inventory = void(*)(AcDbDatabase&, const ga::bridge::DrawingGeometry&, const std::string&);
    using Archive = void(*)(AcDbDatabase&, const std::string&, const std::string&);
    auto capture = reinterpret_cast<Capture>(dlsym(library, "_ZN2ga6bridge29captureLiveSnapshotForServiceERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEERS7_RKNS1_8functionIFvRKNS0_15DrawingGeometryEEEE"));
    auto inventory = reinterpret_cast<Inventory>(dlsym(library, "_ZN2ga9liveQuery14writeInventoryER12AcDbDatabaseRKNS_6bridge15DrawingGeometryERKNSt3__112basic_stringIcNS7_11char_traitsIcEENS7_9allocatorIcEEEE"));
    const auto* archiveSymbol = std::getenv("GA_SEMANTIC_ARCHIVE_SYMBOL");
    auto archive = archiveSymbol ? reinterpret_cast<Archive>(dlsym(library, archiveSymbol)) : nullptr;
    if (!capture || !inventory || !archive) throw std::runtime_error("production capture symbols unavailable");
    AcDbDatabase database(false, true);
    checked(database.readDwgFile(AcString((root / "source/host.dwg").string().c_str()).kwszPtr(), AcDbDatabase::kForReadAndAllShare, true), "fixture read");
    checked(database.closeInput(true), "fixture close input");
    auto* services = acdbHostApplicationServices();
    auto* previous = services->workingDatabase();
    struct Restore { AcDbHostApplicationServices* services; AcDbDatabase* previous; ~Restore() { services->setWorkingDatabase(previous); } } restore{services, previous};
    services->setWorkingDatabase(&database);
    checked(acdbResolveCurrentXRefs(&database, false, false), "fixture resolve");
    const auto snapshot = (root / "Drawing.autocad.json").string();
    std::string error;
    if (!capture(snapshot, error, [&](const ga::bridge::DrawingGeometry& drawing) {
        inventory(database, drawing, snapshot + ".inventory.json");
    })) throw std::runtime_error(error);
    archive(database, (root / "Native").string(), ga::bridge::sha256File(snapshot));
}
void command() {
    const auto* directory = std::getenv("GA_SEMANTIC_ROOT");
    const auto* action = std::getenv("GA_SEMANTIC_ACTION");
    if (!directory || !action) return;
    const auto root = fs::canonical(directory);
    try {
        if (root.string().find("/output/native-semantic-") == std::string::npos)
            throw std::runtime_error("owned semantic fixture directory required");
        if (std::string(action) == "seed") seed(root);
        else if (std::string(action) == "capture") capture(root);
        else throw std::runtime_error("unknown fixture action");
        ga::bridge::writeAtomicText((root / (std::string(action) + "-complete.json")).string(), "{\"completed\":true}");
    } catch (const std::exception& error) {
        ga::bridge::writeAtomicText((root / (std::string(action) + "-error.json")).string(),
            "{\"error\":\"" + ga::bridge::jsonEscape(error.what()) + "\"}");
    }
}
}
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode message, void* id) {
    if (message == AcRx::kInitAppMsg) {
        acrxLoadModule(L"AcGeomentObj.dbx", 0);
        acrxDynamicLinker->loadModule(L"AcBr.dbx", 1);
        acrxDynamicLinker->unlockApplication(id);
        acrxDynamicLinker->registerAppMDIAware(id);
        acedRegCmds->addCommand(L"GA_SEMANTIC_FIXTURE", L"GASEMANTICFIXTURE", L"GASEMANTICFIXTURE", ACRX_CMD_MODAL, command);
    } else if (message == AcRx::kUnloadAppMsg) acedRegCmds->removeGroup(L"GA_SEMANTIC_FIXTURE");
    return AcRx::kRetOK;
}
