// Standalone scratch-only experiment. Never loaded into the user's GUI.
#include "rxregsvc.h"
#include "core_rxmfcapi.h"
#include "aced.h"
#include "adslib.h"
#include "dbapserv.h"
#include "dbents.h"
#include "dbpl.h"
#include "dbsymtb.h"
#include "dbsymutl.h"
#include "acdbxref.h"
#include "acdocman.h"
#include "AcString.h"
#include "dbdate.h"
#include "rxevent.h"
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>
#include <vector>
#include <map>
#include <CommonCrypto/CommonDigest.h>

namespace {
namespace fs = std::filesystem;
std::string utf(const ACHAR* s) { return s ? AcString(s).utf8Str() : ""; }
std::string q(const std::string& s) {
    std::ostringstream o; o << '"';
    for (unsigned char c:s) {
        if(c=='"'||c=='\\') o << '\\' << c;
        else if(c<32) o << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c);
        else o << c;
    }
    o << '"'; return o.str();
}
void ok(Acad::ErrorStatus s,const char* action) {
    if(s!=Acad::eOk) throw std::runtime_error(std::string(action)+":"+std::to_string(int(s)));
}
template<class T> struct Close { void operator()(T* p) const { if(p) p->close(); } };
template<class T> using Ptr=std::unique_ptr<T,Close<T>>;
template<class T> Ptr<T> open(AcDbObjectId id,AcDb::OpenMode mode=AcDb::kForRead) {
    T* p=nullptr; ok(acdbOpenObject(p,id,mode),"open"); return Ptr<T>(p);
}
std::string handle(AcDbObjectId id) {
    ACHAR s[32]={}; id.handle().getIntoAsciiBuffer(s); return utf(s);
}
std::string filename(AcDbDatabase* db) {
    const ACHAR* s=nullptr; if(db) db->getFilename(s); return utf(s);
}
std::string sha(const std::string& file) {
    if(file.empty()||!fs::is_regular_file(file)) return "";
    std::ifstream in(file,std::ios::binary); if(!in) return "";
    CC_SHA256_CTX ctx; CC_SHA256_Init(&ctx); char buffer[65536];
    while(in) { in.read(buffer,sizeof(buffer)); CC_SHA256_Update(&ctx,buffer,static_cast<CC_LONG>(in.gcount())); }
    if(!in.eof()) return "";
    unsigned char hash[CC_SHA256_DIGEST_LENGTH]; CC_SHA256_Final(hash,&ctx);
    std::ostringstream o; for(auto c:hash) o<<std::hex<<std::setw(2)<<std::setfill('0')<<int(c); return o.str();
}
void point(std::ostream& o,AcGePoint3d p) { o << '['<<p.x<<','<<p.y<<','<<p.z<<']'; }
void matrix(std::ostream& o,const AcGeMatrix3d& m) {
    o << '['; for(int i=0;i<4;++i) { o<<(i?",[":"[");
        for(int j=0;j<4;++j) o<<(j?",":"")<<m(i,j); o<<']'; } o<<']';
}
void write(const fs::path& p,const std::string& s) {
    if(fs::exists(p)) throw std::runtime_error("refuse overwrite report: "+p.string());
    std::ofstream out(p); out<<s<<'\n'; if(!out) throw std::runtime_error("write failed");
}
Acad::ErrorStatus save(AcDbDatabase* db,const fs::path& p) {
    if(fs::exists(p)) throw std::runtime_error("refuse overwrite DWG: "+p.string());
    return db->saveAs(AcString(p.string().c_str()).kwszPtr(),false,AcDb::kDHL_CURRENT);
}
Acad::ErrorStatus nativeDxfArchive(AcDbDatabase* db,const fs::path& p) {
    auto dxf=p; dxf.replace_extension(".serialization.dxf");
    if(fs::exists(dxf)) throw std::runtime_error("refuse overwrite DXF");
    auto out=db->dxfOut(AcString(dxf.string().c_str()).kwszPtr(),16,AcDb::kDHL_CURRENT,false);
    if(out!=Acad::eOk) return out;
    AcDbDatabase isolated(false,true);
    auto in=isolated.dxfIn(AcString(dxf.string().c_str()).kwszPtr());
    return in==Acad::eOk?save(&isolated,p):in;
}
void identity(std::ostream& o,AcDbDatabase* db) {
    AcString fingerprint,version;
    if(db) { db->getFingerprintGuid(fingerprint); db->getVersionGuid(version); }
    o<<"{\"filename\":"<<q(filename(db))<<",\"runtime_file_sha256\":"<<q(sha(filename(db)))<<",\"original_filename\":"<<q(db?utf(db->originalFileName()):"")
     <<",\"fingerprint\":"<<q(utf(fingerprint.kwszPtr()))<<",\"version_guid\":"<<q(utf(version.kwszPtr()))
     <<",\"units\":"<<(db?int(db->insunits()):-1)<<",\"runtime_database\":"<<q(std::to_string(reinterpret_cast<uintptr_t>(db)));
    if(db) {
        o<<",\"number_of_saves\":"<<db->numberOfSaves()<<",\"last_saved_version\":"<<int(db->lastSavedAsVersion());
        for(const auto& item:{std::make_pair("tdcreate",db->tdcreate()),std::make_pair("tdupdate",db->tdupdate()),
                             std::make_pair("tdindwg",db->tdindwg()),std::make_pair("tdusrtimer",db->tdusrtimer())})
            o<<','<<q(item.first)<<":["<<item.second.julianDay()<<','<<item.second.msecsPastMidnight()<<']';
    }
    o<<'}';
}
void vars(std::ostream& o) {
    o<<'{'; bool first=true;
    for(const auto* name:{L"DBMOD",L"DWGNAME",L"DWGPREFIX",L"DWGTITLED",L"VIEWCTR",L"VIEWDIR",L"VIEWSIZE",L"VIEWTWIST",L"UCSORG",L"UCSXDIR",L"UCSYDIR",L"ACADVER"}) {
        resbuf value={}; int status=acedGetVar(name,&value);
        o<<(first?"":",")<<q(utf(name))<<":{\"status\":"<<status<<",\"value\":"; first=false;
        if(status!=RTNORM) o<<"null";
        else if(value.restype==RTSTR) { o<<q(utf(value.resval.rstring)); acutDelString(value.resval.rstring); }
        else if(value.restype==RTSHORT) o<<value.resval.rint;
        else if(value.restype==RTLONG) o<<value.resval.rlong;
        else if(value.restype==RTREAL) o<<value.resval.rreal;
        else if(value.restype==RT3DPOINT||value.restype==RTPOINT) point(o,{value.resval.rpoint[0],value.resval.rpoint[1],value.restype==RTPOINT?0:value.resval.rpoint[2]});
        else o<<"null";
        o<<'}';
    } o<<'}';
}
void graph(std::ostream& o,AcDbDatabase* db) {
    AcDbXrefGraph g; auto s=acdbGetHostDwgXrefGraph(db,g,true);
    o<<"{\"status\":"<<int(s)<<",\"nodes\":[";
    for(int i=0;i<g.numNodes();++i) {
        auto* n=g.xrefNode(i);
        o<<(i?",":"")<<"{\"index\":"<<i<<",\"name\":"<<q(utf(n->name()))<<",\"handle\":"<<q(handle(n->btrId()))
         <<",\"runtime_object_id\":"<<q(std::to_string(n->btrId().asOldId()))<<",\"status\":"<<int(n->xrefStatus())
         <<",\"read_substatus\":"<<int(n->xrefReadSubstatus())<<",\"nested\":"<<(n->isNested()?"true":"false")
         <<",\"database_present\":"<<(n->database()?"true":"false")<<",\"database\":";
        identity(o,n->database());
        if(!n->btrId().isNull()) {
            auto record=open<AcDbBlockTableRecord>(n->btrId()); AcString authored,resolved; record->pathName(authored);
            auto found=acdbHostApplicationServices()->findFile(resolved,authored.kwszPtr(),db,AcDbHostApplicationServices::kXRefDrawing);
            o<<",\"authored_path\":"<<q(utf(authored.kwszPtr()))<<",\"resolved_path\":"<<q(utf(resolved.kwszPtr()))
             <<",\"resolve_status\":"<<int(found)<<",\"resolved_disk_sha256\":"<<q(found==Acad::eOk?sha(utf(resolved.kwszPtr())):"");
        }
        o<<",\"children\":[";
        for(int j=0;j<n->numOut();++j) o<<(j?",":"")<<q(utf(static_cast<AcDbXrefGraphNode*>(n->out(j))->name()));
        o<<"]}";
    } o<<"]}";
}
void records(std::ostream& o,AcDbDatabase* db) {
    auto table=open<AcDbBlockTable>(db->blockTableId());
    AcDbBlockTableIterator* p=nullptr; ok(table->newIterator(p),"block iterator");
    std::unique_ptr<AcDbBlockTableIterator> it(p); bool first=true; o<<'[';
    for(;!it->done();it->step()) {
        AcDbObjectId id; ok(it->getRecordId(id),"record id"); auto r=open<AcDbBlockTableRecord>(id);
        if(!r->isFromExternalReference()) continue;
        AcString name,path; r->getName(name); r->pathName(path);
        o<<(first?"":",")<<"{\"handle\":"<<q(handle(id))<<",\"name\":"<<q(utf(name.kwszPtr()))<<",\"path\":"<<q(utf(path.kwszPtr()))
         <<",\"status\":"<<int(r->xrefStatus())<<",\"unloaded\":"<<(r->isUnloaded()?"true":"false")
         <<",\"overlay\":"<<(r->isFromOverlayReference()?"true":"false")<<'}'; first=false;
    } o<<']';
}
void walk(std::ostream& o,AcDbObjectId record,const AcGeMatrix3d& transform,const std::string& route,bool& first,int depth=0) {
    if(depth>8) throw std::runtime_error("fixture depth exceeded");
    auto r=open<AcDbBlockTableRecord>(record); AcDbBlockTableRecordIterator* raw=nullptr;
    ok(r->newIterator(raw),"entity iterator"); std::unique_ptr<AcDbBlockTableRecordIterator> it(raw);
    for(;!it->done();it->step()) {
        AcDbObjectId id; ok(it->getEntityId(id),"entity id"); auto e=open<AcDbEntity>(id);
        auto address=route+handle(id); AcString layer; e->layer(layer);
        o<<(first?"":",")<<"{\"route\":"<<q(address)<<",\"handle\":"<<q(handle(id))<<",\"runtime_object_id\":"<<q(std::to_string(id.asOldId()))
         <<",\"runtime_database\":"<<q(std::to_string(reinterpret_cast<uintptr_t>(e->database())))
         <<",\"class\":"<<q(utf(e->isA()->name()))<<",\"layer\":"<<q(utf(layer.kwszPtr()))<<",\"transform\":";
        matrix(o,transform); first=false;
        if(auto* p=AcDbPolyline::cast(e.get())) {
            o<<",\"closed\":"<<(p->isClosed()?"true":"false")<<",\"vertices\":[";
            for(unsigned j=0;j<p->numVerts();++j) { AcGePoint3d pt; ok(p->getPointAt(j,pt),"vertex");
                o<<(j?",":""); point(o,pt); } o<<"],\"world_vertices\":[";
            for(unsigned j=0;j<p->numVerts();++j) { AcGePoint3d pt; p->getPointAt(j,pt); pt.transformBy(transform);
                o<<(j?",":""); point(o,pt); } o<<']';
        }
        if(auto* l=AcDbLine::cast(e.get())) {
            o<<",\"vertices\":["; point(o,l->startPoint()); o<<','; point(o,l->endPoint());
            o<<"],\"world_vertices\":["; point(o,l->startPoint().transformBy(transform)); o<<','; point(o,l->endPoint().transformBy(transform)); o<<']';
        }
        auto* ref=AcDbBlockReference::cast(e.get());
        if(ref) { auto reference=ref->blockTableRecord(),redirected=reference; redirected.convertToRedirectedId();
                  ACHAR original[32]={}; reference.nonForwardedHandle().getIntoAsciiBuffer(original);
                  o<<",\"reference_record\":"<<q(handle(reference))
                  <<",\"reference_runtime_id\":"<<q(std::to_string(ref->blockTableRecord().asOldId()))
                  <<",\"reference_original_handle\":"<<q(utf(original))
                  <<",\"reference_redirected_runtime_id\":"<<q(std::to_string(redirected.asOldId()))
                  <<",\"block_transform\":"; matrix(o,ref->blockTransform()); }
        o<<'}';
        if(ref) {
            auto child=open<AcDbBlockTableRecord>(ref->blockTableRecord());
            if(!child->isFromExternalReference() || (child->xrefStatus()==AcDb::kXrfResolved&&child->xrefDatabase()))
                walk(o,ref->blockTableRecord(),transform*ref->blockTransform(),address+"/",first,depth+1);
        }
    }
}
std::string snapshot(AcDbDatabase* db) {
    std::ostringstream o; o<<std::setprecision(17)<<"{\"database\":"; identity(o,db);
    o<<",\"document_filename\":"<<q(acDocManager->curDocument()?utf(acDocManager->curDocument()->fileName()):"");
    o<<",\"vars\":"; vars(o); o<<",\"graph\":"; graph(o,db); o<<",\"records\":"; records(o,db);
    o<<",\"entities\":["; bool first=true; walk(o,acdbSymUtil()->blockModelSpaceId(db),AcGeMatrix3d(),"",first); o<<"]}"; return o.str();
}
AcDbObjectId append(AcDbDatabase* db,AcDbEntity* raw) {
    std::unique_ptr<AcDbEntity> e(raw); auto r=open<AcDbBlockTableRecord>(acdbSymUtil()->blockModelSpaceId(db),AcDb::kForWrite);
    AcDbObjectId id; ok(r->appendAcDbEntity(id,e.get()),"append"); e.release()->close(); return id;
}
AcDbObjectId attach(AcDbDatabase* db,const fs::path& file,const wchar_t* name,AcGePoint3d pos,bool overlay=false) {
    AcDbObjectId id; AcString path(file.string().c_str());
    ok(overlay?acdbOverlayXref(db,path.kwszPtr(),name,id):acdbAttachXref(db,path.kwszPtr(),name,id),"attach");
    append(db,new AcDbBlockReference(pos,id)); return id;
}
void seed(const fs::path& root) {
    auto dir=root/"source"; fs::create_directory(dir);
    for(const auto& name:{"nested","offline","missing"}) {
        AcDbDatabase db(true,true); db.setInsunits(AcDb::kUnitsMeters);
        append(&db,new AcDbLine({1,2,3},{4,6,3})); ok(save(&db,dir/(std::string(name)+".dwg")),"save leaf");
    }
    {
        AcDbDatabase db(true,true); db.setInsunits(AcDb::kUnitsMeters);
        append(&db,new AcDbLine({2,3,0},{12,3,0})); attach(&db,dir/"nested.dwg",L"NESTED",{20,30,0});
        ok(save(&db,dir/"child.dwg"),"save child");
    }
    AcDbDatabase db(true,true); db.setInsunits(AcDb::kUnitsMeters);
    auto* p=new AcDbPolyline(); p->addVertexAt(0,{0,0}); p->addVertexAt(1,{10,0});
    p->addVertexAt(2,{10,10}); p->addVertexAt(3,{0,10}); p->setClosed(true); append(&db,p);
    auto child=attach(&db,dir/"child.dwg",L"CHILD",{100,200,0});
    auto* ref=new AcDbBlockReference({-50,60,0},child); ref->setRotation(0.35); ref->setScaleFactors({2,0.5,1}); append(&db,ref);
    auto off=attach(&db,dir/"offline.dwg",L"OFFLINE",{0,1000,0},true);
    AcDbObjectIdArray unload; unload.append(off); ok(acdbUnloadXrefs(&db,unload),"unload");
    attach(&db,dir/"missing.dwg",L"MISSING",{0,2000,0});
    ok(save(&db,dir/"host.dwg"),"save host");
    write(root/"seed.json","{\"completed\":true}");
}
void unsaved(AcDbDatabase* db) {
    auto r=open<AcDbBlockTableRecord>(acdbSymUtil()->blockModelSpaceId(db));
    AcDbBlockTableRecordIterator* raw=nullptr; ok(r->newIterator(raw),"iterator");
    std::unique_ptr<AcDbBlockTableRecordIterator> it(raw); bool found=false;
    for(;!it->done();it->step()) { AcDbObjectId id; it->getEntityId(id); auto e=open<AcDbEntity>(id,AcDb::kForWrite);
        if(auto* p=AcDbPolyline::cast(e.get())) { ok(p->setPointAt(1,{17.125,-2.5}),"unsaved vertex"); found=true; break; } }
    if(!found) throw std::runtime_error("fixture polyline missing");
    r.reset(); append(db,new AcDbLine({777.125,888.25,9},{779.5,889.75,9}));
}
struct CopyReactor:AcRxEventReactor {
    AcDbDatabase* source;
    explicit CopyReactor(AcDbDatabase* db):source(db) { acrxEvent->addReactor(this); }
    ~CopyReactor() override { acrxEvent->removeReactor(this); }
    void wblockNotice(AcDbDatabase* db) override { if(db==source) db->forceWblockDatabaseCopy(); }
};
struct SymbolRestore {
    AcDbDatabase* db; bool active=false;
    explicit SymbolRestore(AcDbDatabase* p):db(p) {}
    ~SymbolRestore() { if(active) db->restoreForwardingXrefSymbols(); }
    Acad::ErrorStatus begin() { auto s=db->restoreOriginalXrefSymbols(); active=s==Acad::eOk; return s; }
    Acad::ErrorStatus end() { if(!active) return Acad::eOk; auto s=db->restoreForwardingXrefSymbols(); active=s!=Acad::eOk; return s; }
};
void unsavedXref(AcDbDatabase* host) {
    AcDbXrefGraph graph; ok(acdbGetHostDwgXrefGraph(host,graph,true),"xref edit graph");
    AcDbDatabase* target=nullptr;
    for(int i=1;i<graph.numNodes();++i) if(utf(graph.xrefNode(i)->name())=="CHILD") target=graph.xrefNode(i)->database();
    if(!target) throw std::runtime_error("fixture CHILD not loaded");
    SymbolRestore symbols(target); ok(symbols.begin(),"restore for scratch XREF edit");
    {
        auto model=open<AcDbBlockTableRecord>(acdbSymUtil()->blockModelSpaceId(target));
        AcDbBlockTableRecordIterator* raw=nullptr; ok(model->newIterator(raw),"xref iterator");
        std::unique_ptr<AcDbBlockTableRecordIterator> it(raw); bool found=false;
        for(;!it->done();it->step()) {
            AcDbObjectId id; it->getEntityId(id); auto e=open<AcDbEntity>(id,AcDb::kForWrite);
            if(auto* line=AcDbLine::cast(e.get())) { ok(line->setEndPoint({13.75,4.125,0}),"unsaved XREF endpoint"); found=true; break; }
        }
        if(!found) throw std::runtime_error("fixture CHILD line missing");
    }
    ok(symbols.end(),"forward after scratch XREF edit");
}
void capture(const fs::path& root,const std::string& variant,bool edit) {
    auto* db=acdbHostApplicationServices()->workingDatabase();
    if(fs::path(filename(db)).parent_path()!=root/"source") throw std::runtime_error("not own source DB");
    write(root/"opened.json",snapshot(db));
    if(edit) { unsaved(db); unsavedXref(db); }
    write(root/"before.json",snapshot(db));
    const auto raw=root/"raw"; fs::create_directory(raw);
    auto status=variant=="native-dxf"?nativeDxfArchive(db,raw/"host.dwg"):save(db,raw/"host.dwg");
    write(root/"after-host.json",snapshot(db));
    std::ostringstream actions; actions<<"{\"host_save_status\":"<<int(status)<<",\"xrefs\":[";
    AcDbXrefGraph g; ok(acdbGetHostDwgXrefGraph(db,g,true),"graph");
    struct Row { AcDbDatabase* db; std::string file; std::string handle; std::string authored; std::string resolved; };
    std::vector<Row> rows; std::set<AcDbDatabase*> seen;
    for(int i=1;i<g.numNodes();++i) { auto* n=g.xrefNode(i);
        if(n->xrefStatus()==AcDb::kXrfResolved&&n->database()&&seen.insert(n->database()).second) {
            auto record=open<AcDbBlockTableRecord>(n->btrId()); AcString authored,resolved; record->pathName(authored);
            acdbHostApplicationServices()->findFile(resolved,authored.kwszPtr(),db,AcDbHostApplicationServices::kXRefDrawing);
            auto h=handle(n->btrId()); rows.push_back({n->database(),"xref-"+h+".dwg",h,utf(authored.kwszPtr()),utf(resolved.kwszPtr())});
        }
    }
    std::ostringstream mapping,unloaded; unloaded<<'['; bool firstUnloaded=true;
    for(const auto& row:rows) mapping<<row.authored<<'\n'<<row.resolved<<'\n'<<row.file<<'\n';
    for(int i=1;i<g.numNodes();++i) {
        auto* n=g.xrefNode(i); if(n->xrefStatus()!=AcDb::kXrfUnloaded||n->btrId().isNull()) continue;
        auto record=open<AcDbBlockTableRecord>(n->btrId()); AcString authored,resolved; record->pathName(authored);
        auto found=acdbHostApplicationServices()->findFile(resolved,authored.kwszPtr(),db,AcDbHostApplicationServices::kXRefDrawing);
        if(found!=Acad::eOk) continue;
        auto source=fs::path(utf(resolved.kwszPtr()));
        if(source.parent_path()!=root/"source") throw std::runtime_error("unloaded copy outside scratch source");
        auto file="unloaded-"+handle(n->btrId())+".dwg";
        auto originalSha=sha(source.string()); fs::copy_file(source,raw/file);
        if(originalSha.empty()||sha(source.string())!=originalSha||sha((raw/file).string())!=originalSha) throw std::runtime_error("unloaded disk copy changed");
        mapping<<utf(authored.kwszPtr())<<'\n'<<utf(resolved.kwszPtr())<<'\n'<<file<<'\n';
        unloaded<<(firstUnloaded?"":",")<<"{\"handle\":"<<q(handle(n->btrId()))<<",\"file\":"<<q(file)
                <<",\"source_sha256\":"<<q(originalSha)<<",\"method\":\"resolved_disk_copy_unloaded_not_loaded_DB\"}"; firstUnloaded=false;
    }
    unloaded<<']'; write(root/"unloaded-copies.json",unloaded.str()); write(root/"archive-map.txt",mapping.str());
    bool first=true;
    for(auto& row:rows) {
        SymbolRestore symbols(row.db);
        const bool needsRestore=variant!="raw";
        auto restore=needsRestore?symbols.begin():Acad::eOk;
        auto copied=Acad::eOk;
        std::unique_ptr<AcDbDatabase> clone;
        if(restore==Acad::eOk&&variant=="restore-wblock") {
            AcDbDatabase* output=nullptr;
            { CopyReactor copy(row.db); copied=row.db->wblock(output); }
            clone.reset(output);
        }
        auto saved=restore!=Acad::eOk?restore:copied!=Acad::eOk?copied:
            variant=="native-dxf"?nativeDxfArchive(row.db,raw/row.file):save(clone?clone.get():row.db,raw/row.file);
        auto forward=needsRestore?symbols.end():Acad::eOk;
        actions<<(first?"":",")<<"{\"handle\":"<<q(row.handle)<<",\"file\":"<<q(row.file)<<",\"authored_path\":"<<q(row.authored)<<",\"resolved_path\":"<<q(row.resolved)
               <<",\"restore_called\":"<<(needsRestore?"true":"false")<<",\"restore_status\":"<<int(restore)<<",\"clone_status\":"<<int(copied)
               <<",\"save_status\":"<<int(saved)<<",\"forward_status\":"<<int(forward)<<'}'; first=false;
        write(root/("after-"+row.file+".json"),snapshot(db));
    }
    actions<<"]}"; write(root/"actions.json",actions.str()); write(root/"after.json",snapshot(db));
}
void repath(const fs::path& root) {
    auto dir=root/"isolated"; fs::create_directory(dir); std::ostringstream o; o<<'['; bool first=true;
    std::map<std::string,std::string> mapping; std::ifstream input(root/"archive-map.txt"); std::string authored,resolved,archive;
    while(std::getline(input,authored)&&std::getline(input,resolved)&&std::getline(input,archive)) {
        mapping[authored]=archive; if(!resolved.empty()) mapping[resolved]=archive;
    }
    for(const auto& f:fs::directory_iterator(root/"raw")) {
        if(f.path().extension()!=".dwg") continue;
        if(f.path().filename().string().rfind("unloaded-",0)==0) {
            // Fixture unloaded leaf has no dependencies. Preserve its authored bytes.
            fs::copy_file(f.path(),dir/f.path().filename()); continue;
        }
        AcDbDatabase db(false,true); ok(db.readDwgFile(AcString(f.path().string().c_str()).kwszPtr()),"read raw"); db.closeInput(true);
        auto table=open<AcDbBlockTable>(db.blockTableId()); AcDbBlockTableIterator* raw=nullptr; ok(table->newIterator(raw),"iterator");
        std::unique_ptr<AcDbBlockTableIterator> it(raw);
        for(;!it->done();it->step()) {
            AcDbObjectId id; it->getRecordId(id); auto r=open<AcDbBlockTableRecord>(id,AcDb::kForWrite);
            if(!r->isFromExternalReference()) continue;
            AcString p; r->pathName(p); auto leaf=fs::path(utf(p.kwszPtr())).filename();
            // Use the captured path-to-database map, never cache filenames or basenames.
            auto found=mapping.find(utf(p.kwszPtr()));
            auto relative=found!=mapping.end()?"./"+found->second:"./_unavailable/"+leaf.string();
            auto s=r->setPathName(AcString(relative.c_str()).kwszPtr());
            o<<(first?"":",")<<"{\"file\":"<<q(f.path().filename().string())<<",\"handle\":"<<q(handle(id))
             <<",\"before\":"<<q(utf(p.kwszPtr()))<<",\"after\":"<<q(relative)<<",\"status\":"<<int(s)<<'}'; first=false;
            ok(s,"repath");
        }
        it.reset(); table.reset(); ok(save(&db,dir/f.path().filename()),"save isolated");
    }
    o<<']'; write(root/"repath.json",o.str());
}
void command() {
    ACHAR arg[4096]={}; if(acedGetString(1,L"\nScratch request: ",arg)!=RTNORM) return;
    fs::path root;
    try {
        std::ifstream in(utf(arg)); std::string mode,dir,variant,edit; std::getline(in,mode); std::getline(in,dir); std::getline(in,variant); std::getline(in,edit);
        root=fs::canonical(dir);
        if(root.string().find("/artifacts/native-session-capture-20260923/")==std::string::npos) throw std::runtime_error("scratch root required");
        if(mode=="seed") seed(root);
        else if(mode=="capture") capture(root,variant,edit!="clean");
        else if(mode=="repath") repath(root);
        else if(mode=="inspect") write(root/(variant+".json"),snapshot(acdbHostApplicationServices()->workingDatabase()));
        else throw std::runtime_error("unknown mode");
        acutPrintf(L"\nCAPTURE_SESSION_COMPLETE");
    } catch(const std::exception& e) {
        if(!root.empty()) { std::ofstream error(root/"native-error.txt",std::ios::app); error<<e.what()<<'\n'; }
        acutPrintf(L"\nCAPTURE_SESSION_ERROR: %s",AcString(e.what()).kwszPtr());
    }
}
}
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode m,void* id) {
    if(m==AcRx::kInitAppMsg) {
        acrxDynamicLinker->unlockApplication(id); acrxDynamicLinker->registerAppMDIAware(id);
        acedRegCmds->addCommand(L"GA_CAPTURE_SESSION_PROBE",L"GACAPTURESESSIONPROBE",L"GACAPTURESESSIONPROBE",ACRX_CMD_MODAL,command);
    } else if(m==AcRx::kUnloadAppMsg) acedRegCmds->removeGroup(L"GA_CAPTURE_SESSION_PROBE");
    return AcRx::kRetOK;
}
