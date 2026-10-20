// Проверяет производственный сборщик CAD-областей на контрольных кривых и захвате.
#include "rxregsvc.h"
#include "core_rxmfcapi.h"
#include "aced.h"
#include "adslib.h"
#include "AcString.h"
#include "planar_faces.h"
#include "native_face_catalog.h"
#include "xref_instance_access.h"
#include "file_io.h"
#include "dbents.h"
#include "dbpl.h"
#include "dbhatch.h"
#include "dbsymutl.h"
#include "dbsymtb.h"
#include "dbtable.h"
#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace {
std::string q(const std::string& s){return "\""+ga::bridge::jsonEscape(s)+"\"";}
std::string line(std::istream& in){std::string s;if(!std::getline(in,s)||s.empty())throw std::runtime_error("request line");return s;}
void expect(bool ok,const char* message){if(!ok)throw std::runtime_error(message);}
void add(std::vector<ga::faces::Source>& sources,std::string route,double x1,double y1,double x2,double y2,bool clipping=false) {
    sources.push_back({route,clipping?"cut":"physical",std::make_unique<AcDbLine>(AcGePoint3d(x1,y1,0),AcGePoint3d(x2,y2,0)),clipping});
}
void shortArcControl(double scale,const AcGePoint3d& center) {
    // Дуга с хордой образуют настоящую область, даже когда угол меньше шага отображения.
    constexpr double sweep=.025;
    const double radius=.04*scale;
    auto arc=std::make_unique<AcDbArc>(center,AcGeVector3d::kZAxis,radius,0,sweep);
    AcGePoint3d start,end;
    expect(arc->getStartPoint(start)==Acad::eOk&&arc->getEndPoint(end)==Acad::eOk,"short arc endpoints");
    std::vector<ga::faces::Source> sources;
    sources.push_back({"arc","physical",std::move(arc),false});
    sources.push_back({"chord","physical",std::make_unique<AcDbLine>(end,start),false});
    auto result=ga::faces::assemble(std::move(sources),scale);
    expect(result.issues.empty()&&result.faces.size()==1,"short arc native face retained");
    auto& face=result.faces.front();
    const double expectedArea=radius*radius*(sweep-std::sin(sweep))/2;
    expect(std::abs(face.query->evidence().localArea-expectedArea)<1e-12*scale*scale,"short arc exact native area");
    expect(face.display.size()>=4&&face.display.front()==face.display.back(),"short arc display has a closed polygon");
    const auto origin=face.display.front();
    double twiceArea=0;
    for(std::size_t i=1;i<face.display.size();++i) {
        const auto a=face.display[i-1]-origin,b=face.display[i]-origin;
        twiceArea+=a.x*b.y-b.x*a.y;
    }
    expect(twiceArea>0,"short arc display retains a nondegenerate interior");
    const double expectedSagitta=2*radius*std::pow(std::sin(sweep/8),2);
    expect(face.samplingToleranceUnits>=expectedSagitta
        &&face.samplingToleranceUnits<=expectedSagitta+3e-8,"short arc display reports sampling and join error");
    expect(face.routes==std::set<std::string>({"arc","chord"})&&face.physicalRoutes==face.routes
        &&face.repairs.empty(),"short arc physical provenance unchanged");
    expect(face.query->evidence().edgeCount==0,"short arc display does not prepare distance queries");
}
unsigned instanceControls() {
    // Отдельная база в памяти: отражение и неравномерный масштаб без изменения CAD пользователя.
    AcDbDatabase database(true,true);
    AcDbBlockTable* table=nullptr;
    expect(database.getBlockTable(table,AcDb::kForWrite)==Acad::eOk,"instance control block table");
    const auto record=[&](const ACHAR* name) {
        auto* value=new AcDbBlockTableRecord;
        value->setName(name);
        AcDbObjectId id;
        expect(table->add(id,value)==Acad::eOk,"instance control block record");
        value->close();return id;
    };
    const auto inner=record(L"GA_INSTANCE_INNER"),outer=record(L"GA_INSTANCE_OUTER");
    table->close();
    const auto append=[&](AcDbObjectId owner,AcDbEntity* entity) {
        AcDbBlockTableRecord* value=nullptr;
        expect(acdbOpenObject(value,owner,AcDb::kForWrite)==Acad::eOk,"instance control owner");
        AcDbObjectId id;
        expect(value->appendAcDbEntity(id,entity)==Acad::eOk,"instance control entity");
        entity->close();value->close();return id;
    };
    const auto leaf=append(inner,new AcDbCircle({2,3,0},AcGeVector3d::kZAxis,1));
    auto* nested=new AcDbBlockReference({4,5,0},inner);
    expect(nested->setScaleFactors({2,-3,1})==Acad::eOk,"instance control reflected scale");
    expect(nested->setRotation(std::acos(-1.)/2)==Acad::eOk,"instance control rotation");
    const auto child=append(outer,nested);
    auto* root=new AcDbBlockReference({10,20,0},outer);
    expect(root->setScaleFactors({3,2,1})==Acad::eOk,"instance control nonuniform scale");
    const auto parent=append(acdbSymUtil()->blockModelSpaceId(&database),root);
    const auto handle=[](AcDbObjectId id) {
        ACHAR text[32]{};id.nonForwardedHandle().getIntoAsciiBuffer(text);return std::string(AcString(text).utf8Str());
    };
    const auto route=handle(parent)+"/"+handle(child)+"/"+handle(leaf);
    ga::xref::Instance instance;ga::xref::resolve(database,route,instance);
    expect(instance.entity->objectId()==leaf&&instance.parents.length()==2,"instance control exact ownership");
    AcGePoint3d point(2,3,0);point.transformBy(instance.transform);
    expect(point.distanceTo({49,38,0})<1e-8,"instance control native matrix order and reflection");
    expect(instance.compoundMatrixDelta<1e-8,"instance control native matrix agreement");
    const auto rejected=[&](const std::string& wrong) {
        try {ga::xref::Instance result;ga::xref::resolve(database,wrong,result);return false;}
        catch(const std::exception&) {return true;}
    };
    expect(rejected(handle(parent)+"/"+handle(leaf)),"instance control rejects missing parent");
    expect(rejected(handle(leaf)),"instance control rejects foreign owner");
    expect(rejected(route+"/"),"instance control rejects malformed route");
    auto* annotation=new AcDbTable;
    expect(annotation->setSize(2,2)==Acad::eOk,"annotation control table size");
    expect(annotation->setRowHeight(1)==Acad::eOk,"annotation control row height");
    expect(annotation->setColumnWidth(2)==Acad::eOk,"annotation control column width");
    const auto annotationId=append(acdbSymUtil()->blockModelSpaceId(&database),annotation);
    AcDbTable* preparedTable=nullptr;
    expect(acdbOpenObject(preparedTable,annotationId,AcDb::kForWrite)==Acad::eOk,"annotation control native table");
    expect(preparedTable->recomputeTableBlock()==Acad::eOk,"annotation control native layout");
    const auto annotationBlock=preparedTable->blockTableRecord();preparedTable->close();
    AcDbBlockTableRecord* layout=nullptr;
    expect(acdbOpenObject(layout,annotationBlock,AcDb::kForRead)==Acad::eOk,"annotation control layout record");
    AcDbBlockTableRecordIterator* children=nullptr;
    expect(layout->newIterator(children)==Acad::eOk&&!children->done(),"annotation control actual child");
    AcDbObjectId annotationLeaf;
    expect(children->getEntityId(annotationLeaf)==Acad::eOk,"annotation control child id");
    delete children;layout->close();
    const auto annotationContext=[&](const std::string& value) {
        try {ga::xref::Instance result;ga::xref::resolve(database,value,result);return false;}
        catch(const ga::xref::NonCalculationContext&) {return true;}
        catch(const std::exception&) {return false;}
    };
    expect(annotationContext(handle(annotationId)+"/"+handle(annotationLeaf)),"annotation control excludes owned table child");
    const auto foreign=handle(annotationId)+"/"+handle(leaf);
    expect(rejected(foreign)&&!annotationContext(foreign),"annotation control foreign leaf remains failure");
    return 6;
}
enum class CompanionShape { Curve, Region, Hatch };
std::unique_ptr<AcDbEntity> boundaryStrip(CompanionShape shape,bool coincident=false) {
    if(shape==CompanionShape::Curve) return coincident
        ?std::make_unique<AcDbLine>(AcGePoint3d(0,10,0),AcGePoint3d(0,0,0))
        :std::make_unique<AcDbLine>(AcGePoint3d(10,0,0),AcGePoint3d(0,0,0));
    const std::vector<AcGePoint2d> points=coincident
        ?std::vector<AcGePoint2d>{{-1,0},{0,0},{0,10},{-1,10}}
        :std::vector<AcGePoint2d>{{0,-1},{10,-1},{10,0},{0,0}};
    if(shape==CompanionShape::Hatch) {
        auto hatch=std::make_unique<AcDbHatch>();
        expect(hatch->setPattern(AcDbHatch::kPreDefined,L"SOLID")==Acad::eOk,"companion fixture hatch pattern");
        AcGePoint2dArray vertices;AcGeDoubleArray bulges;
        for(const auto& point:points) {vertices.append(point);bulges.append(0);}
        expect(hatch->appendLoop(AcDbHatch::kExternal|AcDbHatch::kPolyline,vertices,bulges)==Acad::eOk,
            "companion fixture hatch loop");
        expect(hatch->evaluateHatch()==Acad::eOk,"companion fixture hatch evaluation");
        return hatch;
    }
    AcDbPolyline outline;
    for(unsigned i=0;i<points.size();++i) expect(outline.addVertexAt(i,points[i])==Acad::eOk,"companion fixture region vertex");
    outline.setClosed(true);
    expect(outline.isClosed(),"companion fixture region outline");
    AcArray<AcDbEntity*> curves;curves.append(&outline);
    AcArray<AcDbRegion*> regions;
    const auto status=AcDbRegion::createFromCurves(curves,regions);
    std::vector<std::unique_ptr<AcDbRegion>> owned;
    for(auto* region:regions) owned.emplace_back(region);
    expect(status==Acad::eOk&&owned.size()==1,"companion fixture region creation");
    return std::move(owned.front());
}
unsigned topologyCompanionControls(CompanionShape shape) {
    // Три физических стороны и одна граница другого слоя, без правки CAD пользователя.
    AcDbDatabase database(true,true);
    expect(database.setInsunits(AcDb::kUnitsMeters)==Acad::eOk,"companion fixture units");
    AcDbLayerTable* layers=nullptr;
    expect(database.getLayerTable(layers,AcDb::kForWrite)==Acad::eOk,"companion fixture layers");
    const auto layer=[&](const ACHAR* name) {
        auto* value=new AcDbLayerTableRecord;
        expect(value->setName(name)==Acad::eOk,"companion fixture layer name");
        AcDbObjectId id;
        expect(layers->add(id,value)==Acad::eOk,"companion fixture layer");
        value->close();return id;
    };
    const auto physicalLayer=layer(L"PHYSICAL"),boundaryLayer=layer(L"BOUNDARY");
    layers->close();
    AcDbBlockTableRecord* model=nullptr;
    expect(acdbOpenObject(model,acdbSymUtil()->blockModelSpaceId(&database),AcDb::kForWrite)==Acad::eOk,
        "companion fixture model space");
    ga::bridge::DrawingGeometry drawing;
    std::set<std::string> physicalRoutes;
    const auto append=[&](std::unique_ptr<AcDbEntity> entity,bool boundary) {
        expect(entity->setLayer(boundary?boundaryLayer:physicalLayer)==Acad::eOk,"companion fixture curve layer");
        AcDbObjectId id;
        expect(model->appendAcDbEntity(id,entity.get())==Acad::eOk,"companion fixture entity");
        ACHAR handle[32]{};id.nonForwardedHandle().getIntoAsciiBuffer(handle);
        const std::string route=AcString(handle).utf8Str();
        ga::bridge::EntityCoverage row;
        row.handle=route;row.entityType=AcString(entity->isA()->name()).utf8Str();row.status="native";
        row.sourceLayer=row.layer=boundary?"BOUNDARY":"PHYSICAL";
        drawing.coverage.push_back(std::move(row));
        if(!boundary) physicalRoutes.insert(route);
        entity.release()->close();return route;
    };
    append(std::make_unique<AcDbLine>(AcGePoint3d(0,0,0),AcGePoint3d(0,10,0)),false);
    append(std::make_unique<AcDbLine>(AcGePoint3d(0,10,0),AcGePoint3d(10,10,0)),false);
    append(std::make_unique<AcDbLine>(AcGePoint3d(10,10,0),AcGePoint3d(10,0,0)),false);
    const auto boundary=append(boundaryStrip(shape),true);
    model->close();
    const std::set<std::string> physical{"PHYSICAL"},companions{"BOUNDARY"},empty;
    ga::faces::rememberFaceSources(database,drawing);
    ga::faces::prepareFaceLayers(database,physical,{},&companions);
    const auto anchor=*physicalRoutes.begin();
    const auto answer=ga::faces::queryFace(database,1,anchor,{5,5,0});
    expect(answer.membership=="occupied","typed companion closes physical outline");
    auto region=ga::faces::copyFaceRegion(database,1,anchor);
    double area=0;expect(region&&region->getArea(area)==Acad::eOk&&std::abs(area-100)<1e-8,
        "typed companion exact native area");
    std::ostringstream serialized;ga::faces::emitCatalog(serialized,physical);
    const auto catalogue=serialized.str();
    expect(catalogue.find("\"face_policy\":\"native-planar-faces/2\"")!=std::string::npos
        &&catalogue.find("\"companion_layers\":[\"BOUNDARY\"]")!=std::string::npos
        &&catalogue.find(q(boundary))!=std::string::npos,"typed companion provenance retained");
    ga::faces::rememberFaceSources(database,drawing);
    ga::faces::prepareFaceLayers(database,physical,{},&empty);
    std::ostringstream without;ga::faces::emitCatalog(without,physical);
    expect(without.str().find("\"faces\":[]")!=std::string::npos,"unrequested cross-layer curve is not used");
    const std::set<std::string> absent{"ABSENT"};
    ga::faces::rememberFaceSources(database,drawing);
    ga::faces::prepareFaceLayers(database,physical,{},&absent);
    std::ostringstream missing;ga::faces::emitCatalog(missing,physical);
    expect(missing.str().find("\"faces\":[]")!=std::string::npos,"absent companion does not invent an area");
    ga::faces::rememberFaceSources(database,drawing);
    ga::faces::prepareFaceLayers(database,empty,{},&companions);
    std::ostringstream initial;ga::faces::emitCatalog(initial,empty);
    expect(initial.str().find("\"faces\":[]")!=std::string::npos
        &&initial.str().find("\"face_policy\":\"native-planar-faces/2\"")!=std::string::npos
        &&initial.str().find("\"companion_layers\":[\"BOUNDARY\"]")!=std::string::npos,
        "capture before physical classification retains typed recipe without areas");
    auto unreadable=drawing;
    for(auto& row:unreadable.coverage) if(row.layer=="BOUNDARY") row.handle="FFFFFFFF";
    ga::faces::rememberFaceSources(database,unreadable);
    ga::faces::prepareFaceLayers(database,physical,{},&companions);
    std::ostringstream incomplete;ga::faces::emitCatalog(incomplete,physical);
    expect(incomplete.str().find("\"faces\":[]")!=std::string::npos
        &&incomplete.str().find("FFFFFFFF: ")!=std::string::npos,
        "unreadable companion retains an addressed issue without discarding the project");
    ga::faces::rememberFaceSources(database,drawing);
    ga::faces::prepareFaceLayers(database,physical);
    std::ostringstream legacy;ga::faces::emitCatalog(legacy,physical);
    ga::faces::rememberFaceSources(database,drawing);
    ga::faces::prepareFaceLayers(database,physical,{},nullptr);
    std::ostringstream legacyExplicit;ga::faces::emitCatalog(legacyExplicit,physical);
    expect(legacy.str()==legacyExplicit.str()
        &&legacy.str().find("\"face_policy\":\"native-planar-faces/1\"")!=std::string::npos
        &&legacy.str().find("companion_layers")==std::string::npos,
        "default and explicit legacy policy retain the same catalogue bytes");
    expect(acdbOpenObject(model,acdbSymUtil()->blockModelSpaceId(&database),AcDb::kForWrite)==Acad::eOk,
        "coincident companion root owner");
    const auto coincident=append(boundaryStrip(shape,true),true);
    model->close();
    ga::faces::rememberFaceSources(database,drawing);
    ga::faces::prepareFaceLayers(database,physical,{},&companions);
    std::ostringstream merged;ga::faces::emitCatalog(merged,physical);
    std::string expectedPhysical="\"physical_routes\":[";
    bool firstPhysical=true;
    for(const auto& route:physicalRoutes) {
        expectedPhysical+=(firstPhysical?"":",")+q(route);firstPhysical=false;
    }
    expectedPhysical+=']';
    expect(merged.str().find(expectedPhysical)!=std::string::npos
        &&merged.str().find(q(coincident))!=std::string::npos
        &&ga::faces::queryFace(database,1,anchor,{5,5,0}).membership=="occupied",
        "coincident companion retains provenance without acquiring a physical role");
    AcDbBlockTable* blocks=nullptr;
    expect(database.getBlockTable(blocks,AcDb::kForWrite)==Acad::eOk,"foreign companion block table");
    auto* block=new AcDbBlockTableRecord;
    block->setName(L"GA_FOREIGN_COMPANION");
    AcDbObjectId blockId;
    expect(blocks->add(blockId,block)==Acad::eOk,"foreign companion block");
    block->close();blocks->close();
    expect(acdbOpenObject(model,blockId,AcDb::kForWrite)==Acad::eOk,"foreign companion owner");
    append(boundaryStrip(shape),true);
    model->close();
    expect(acdbOpenObject(model,acdbSymUtil()->blockModelSpaceId(&database),AcDb::kForWrite)==Acad::eOk,
        "foreign companion root owner");
    auto* parent=new AcDbBlockReference(AcGePoint3d::kOrigin,blockId);
    AcDbObjectId parentId;
    expect(model->appendAcDbEntity(parentId,parent)==Acad::eOk,"foreign companion instance");
    ACHAR parentHandle[32]{};parentId.nonForwardedHandle().getIntoAsciiBuffer(parentHandle);
    parent->close();model->close();
    drawing.coverage.back().instanceChain.push_back(AcString(parentHandle).utf8Str());
    drawing.coverage.erase(std::remove_if(drawing.coverage.begin(),drawing.coverage.end(),
        [&](const auto& row) {return row.handle==boundary;}),drawing.coverage.end());
    ga::faces::rememberFaceSources(database,drawing);
    ga::faces::prepareFaceLayers(database,physical,{},&companions);
    std::ostringstream separated;ga::faces::emitCatalog(separated,physical);
    expect(separated.str().find("\"faces\":[]")!=std::string::npos
        &&separated.str().find("\"face_issues\":[]")!=std::string::npos,
        "a readable boundary from another instance does not close the physical outline");
    ga::faces::clearCatalog();
    std::vector<ga::faces::Source> mostlyBoundary;
    add(mostlyBoundary,"1",0,0,0,10);
    add(mostlyBoundary,"2",0,10,10,10,true);
    add(mostlyBoundary,"3",10,10,10,0,true);
    add(mostlyBoundary,"4",10,0,0,0,true);
    expect(ga::faces::assemble(std::move(mostlyBoundary),1).faces.empty(),
        "boundary majority cannot fill whole territory");
    return 9;
}
unsigned selftest() {
    unsigned count=instanceControls();
    for(const auto shape:{CompanionShape::Curve,CompanionShape::Region,CompanionShape::Hatch})
        count+=topologyCompanionControls(shape);
    for(const double scale:{1.,1000.}) {
        for(const double gap:{0.,.01,.03}) {
            std::vector<ga::faces::Source> sources;
            add(sources,"1",0,0,0,10*scale);
            add(sources,"2",0,10*scale,10*scale,10*scale);
            add(sources,"3",10*scale,10*scale,10*scale,gap*scale);
            add(sources,"4",-5*scale,0,15*scale,0,true);
            auto result=ga::faces::assemble(std::move(sources),scale);
            expect(result.faces.size()==(gap<=.02?1:0),"bounded face/metric gap control");
            if(!result.faces.empty()) {
                expect(std::abs(result.faces[0].query->evidence().localArea-100*scale*scale)<.001,"area control");
                auto& query=*result.faces[0].query;
                expect(query.evidence().edgeCount==0,"catalog does not eagerly build distances");
                const auto membership=query.membershipPlanar({5*scale,5*scale,0});
                expect(membership.membership=="occupied"&&!membership.distanceComplete,"native membership-only control");
                expect(query.evidence().edgeCount==0,"containment does not trigger distance preparation");
                const auto answer=query.queryPlanar({5*scale,5*scale,0});
                expect(answer.membership=="occupied"&&answer.distanceComplete&&std::abs(answer.distance-5*scale)<1e-7,"deferred distance control");
                expect(query.evidence().edgeCount>0,"first distance query builds the oracle");
                expect(result.faces[0].query->queryPlanar({20*scale,5*scale,0}).membership=="outside","outside control");
            }
            ++count;
        }
    }
    std::vector<ga::faces::Source> sources;
    auto poly=std::make_unique<AcDbPolyline>();
    const std::vector<AcGePoint2d> points={{0,0},{10,0},{10,10},{0,10},{0,0},{-1,0}};
    for(unsigned i=0;i<points.size();++i) poly->addVertexAt(i,points[i]);
    sources.push_back({"1","building",std::move(poly),false});
    auto tail=ga::faces::assemble(std::move(sources),1);
    expect(tail.faces.size()==1&&std::abs(tail.faces[0].query->evidence().localArea-100)<1e-8,"loop with tail control");
    ++count;
    for(const double scale:{1.,1000.}) {
        const double radius=10*scale,pi=std::acos(-1.);
        std::vector<ga::faces::Source> arcs;
        arcs.push_back({"a","curve",std::make_unique<AcDbArc>(AcGePoint3d::kOrigin,
            AcGeVector3d::kZAxis,radius,0,pi),false});
        arcs.push_back({"b","curve",std::make_unique<AcDbArc>(AcGePoint3d::kOrigin,
            AcGeVector3d::kZAxis,radius,pi,2*pi),false});
        auto curved=ga::faces::assemble(std::move(arcs),scale);
        expect(curved.faces.size()==1,"arc face control");
        auto& lazy=*curved.faces[0].query;
        expect(lazy.evidence().edgeCount==0,"arc distance preparation is deferred");
        std::vector<AcDbEntity*> curves;
        for(auto& fragment:curved.faces[0].fragments) curves.push_back(fragment.curve.get());
        ga::nativeQuery::AreaGroupQuery eager;
        eager.prepare(curves,AcGeMatrix3d::kIdentity);
        expect(std::abs(eager.evidence().localArea-lazy.evidence().localArea)<1e-7*scale*scale,"eager/lazy native areas agree");
        for(const auto point:{AcGePoint3d(0,0,0),AcGePoint3d(radius,0,0),AcGePoint3d(2*radius,0,0)}) {
            const auto a=lazy.queryPlanar(point),b=eager.queryPlanar(point);
            expect(a.status==b.status&&a.membership==b.membership&&a.distanceComplete&&b.distanceComplete
                &&std::abs(a.distance-b.distance)<1e-7,"eager/lazy native curved distances agree");
        }
        ++count;
    }
    for(const double scale:{1.,1000.}) {
        for(const auto center:{AcGePoint3d::kOrigin,AcGePoint3d(17761*scale,14955*scale,0)}) {
            shortArcControl(scale,center);
            ++count;
        }
    }
    return count;
}
void command() {
    ACHAR path[4096]={};if(acedGetString(1,_T("\nProduction face test request: "),path)!=RTNORM)return;
    try {
        std::ifstream in(AcString(path).utf8Str());const auto output=line(in);
        expect(!std::ifstream(output).good(),"output exists");
        const auto count=std::stoi(line(in));expect(count>=0&&count<10000,"input count");
        auto* host=acdbHostApplicationServices()->workingDatabase();
        resbuf before{},after{};acedGetVar(_T("DBMOD"),&before);
        if(count==0) {
            const auto controls=selftest();
            acedGetVar(_T("DBMOD"),&after);expect(before.resval.rint==after.resval.rint,"controls changed DBMOD");
            expect(ga::bridge::writeAtomicText(output,"{\"selftests\":"+std::to_string(controls)+",\"passed\":true}"),"publish controls");
            return;
        }
        std::vector<ga::faces::Source> sources;
        for(int i=0;i<count;++i) {
            const auto route=line(in),layer=line(in);
            ga::xref::Instance instance;ga::xref::resolve(*host,route,instance);
            auto* curve=AcDbCurve::cast(instance.entity);expect(curve,"input is curve");
            AcDbEntity* transformed=nullptr;
            expect(curve->getTransformedCopy(instance.transform,transformed)==Acad::eOk,"world clone");
            auto* world=AcDbCurve::cast(transformed);expect(world,"world curve");
            sources.push_back({route,layer,std::unique_ptr<AcDbCurve>(world),layer.find("Граница заказа")!=std::string::npos});
        }
        std::ostringstream out;out<<std::setprecision(17)<<"{\"selftests\":"<<selftest();
        auto result=ga::faces::assemble(std::move(sources),1);
        out<<",\"issues\":[";
        for(std::size_t i=0;i<result.issues.size();++i)out<<(i?",":"")<<q(result.issues[i]);
        out<<"],\"faces\":[";
        for(std::size_t i=0;i<result.faces.size();++i) {
            const auto& face=result.faces[i];out<<(i?",":"")<<"{\"area\":"<<face.query->evidence().localArea<<",\"routes\":[";
            bool first=true;for(const auto& route:face.routes){out<<(first?"":",")<<q(route);first=false;}
            out<<"],\"repairs\":"<<face.repairs.size()<<",\"controls\":[";
            const std::vector<AcGePoint3d> controls={{15986,-4975,0},{15994.606306,-5245.249309,0},{16087,-5231,0},{15610,-5000,0}};
            for(std::size_t c=0;c<controls.size();++c)out<<(c?",":"")<<q(face.query->queryPlanar(controls[c]).membership);
            out<<"]}";
        }
        out<<"],\"hatch_clone_checks\":[";
        for(unsigned i=0;i<2;++i) {
            const std::string route=i?"6DE6/2DCAD9":"6DE6/2918D5";
            ga::xref::Instance instance;ga::xref::resolve(*host,route,instance);
            auto* original=AcDbHatch::cast(instance.entity);expect(original,"hatch source");
            std::unique_ptr<AcDbHatch> clone(AcDbHatch::cast(original->clone()));expect(bool(clone),"hatch clone");
            const auto status=clone->evaluateHatch();
            std::unique_ptr<AcDbRegion> region(clone->getRegionArea());
            out<<(i?",":"")<<"{\"route\":"<<q(route)<<",\"evaluate_status\":"<<int(status)<<",\"region\":"<<(region?"true":"false")<<'}';
        }
        acedGetVar(_T("DBMOD"),&after);expect(before.resval.rint==after.resval.rint,"source DBMOD changed");
        out<<"],\"dbmod_before\":"<<before.resval.rint<<",\"dbmod_after\":"<<after.resval.rint<<'}';
        expect(ga::bridge::writeAtomicText(output,out.str()),"publish");
        acutPrintf(_T("\nProduction face pipeline controls completed."));
    } catch(const std::exception& e) {acutPrintf(_T("\nProduction face controls failed: %s"),AcString(e.what()).kwszPtr());}
}
}
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode msg,void* id) {
    if(msg==AcRx::kInitAppMsg) {
        acrxLoadModule(_T("AcGeomentObj.dbx"),0);acrxDynamicLinker->loadModule(_T("AcBr.dbx"),1);
        acrxDynamicLinker->unlockApplication(id);acrxDynamicLinker->registerAppMDIAware(id);
        acedRegCmds->addCommand(_T("GA_FACE_PIPELINE_TEST"),_T("GAFACEAUDIT"),_T("GAFACEAUDIT"),ACRX_CMD_MODAL,command);
    } else if(msg==AcRx::kUnloadAppMsg) acedRegCmds->removeGroup(_T("GA_FACE_PIPELINE_TEST"));
    return AcRx::kRetOK;
}
