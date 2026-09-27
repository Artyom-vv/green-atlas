// Diagnostic only: inspect the actual AutoCAD host database and loaded XREFs.
// No separately reopened XREF fallback, no geometry sampling, no source save.
#include "file_io.h"
#include "rxregsvc.h"
#include "core_rxmfcapi.h"
#include "aced.h"
#include "adslib.h"
#include "dbapserv.h"
#include "dbents.h"
#include "dbsymtb.h"
#include "dbsymutl.h"
#include "acdbxref.h"
#include "AcString.h"
#include "xref_line_controls.h"
#include "xref_path_probe.h"
#include "xref_area_controls.h"
#include "xref_window_inventory.h"
#include <algorithm>
#include <chrono>
#include <fstream>
#include <iomanip>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>
#include <vector>

namespace {
std::string utf(const ACHAR* s) { return s ? AcString(s).utf8Str() : ""; }
std::string q(const std::string& s) { return "\""+ga::bridge::jsonEscape(s)+"\""; }
std::string filename(AcDbDatabase* db) {
    const ACHAR* value=nullptr;
    return db && db->getFilename(value)==Acad::eOk ? utf(value) : "";
}
std::string handle(const AcDbObject* object) {
    AcDbHandle h; object->getAcDbHandle(h); ACHAR s[32]={}; h.getIntoAsciiBuffer(s); return utf(s);
}
std::string handle(AcDbObjectId id) { AcDbHandle h=id.handle(); ACHAR s[32]={}; h.getIntoAsciiBuffer(s); return utf(s); }
void matrix(std::ostream& out,const AcGeMatrix3d& m) {
    out << '[';
    for(int i=0;i<4;++i) { if(i) out << ','; out << '[';
        for(int j=0;j<4;++j) out << (j ? "," : "") << m(i,j); out << ']'; }
    out << ']';
}
void chain(std::ostream& out,const std::vector<std::string>& values) {
    out << '['; for(std::size_t i=0;i<values.size();++i) out << (i ? "," : "") << q(values[i]); out << ']';
}
template<class T> struct Close { void operator()(T* p) const { if(p) p->close(); } };
template<class T> using Read=std::unique_ptr<T,Close<T>>;
template<class T> Read<T> open(AcDbObjectId id) {
    T* p=nullptr;
    if(acdbOpenObject(p,id,AcDb::kForRead)!=Acad::eOk || !p) throw std::runtime_error("open object "+handle(id));
    return Read<T>(p);
}
void graph(std::ostream& out,AcDbDatabase* db) {
    AcDbXrefGraph g;
    const auto status=acdbGetHostDwgXrefGraph(db,g,true);
    out << "{\"status\":" << int(status) << ",\"nodes\":[";
    for(int i=0;i<g.numNodes();++i) {
        auto* n=g.xrefNode(i);
        out << (i ? "," : "") << "{\"index\":" << i << ",\"name\":" << q(utf(n->name()))
            << ",\"record\":" << q(handle(n->btrId())) << ",\"status\":" << int(n->xrefStatus())
            << ",\"read_substatus\":" << int(n->xrefReadSubstatus())
            << ",\"nested\":" << (n->isNested()?"true":"false")
            << ",\"database_present\":" << (n->database()?"true":"false")
            << ",\"database_file\":" << q(filename(n->database())) << ",\"children\":[";
        for(int j=0;j<n->numOut();++j) {
            auto* child=static_cast<AcDbXrefGraphNode*>(n->out(j));
            out << (j?",":"") << q(utf(child->name()));
        }
        out << "]}";
    }
    out << "]}";
}
void records(std::ostream& out,AcDbDatabase* db) {
    AcDbBlockTable* raw=nullptr; if(db->getBlockTable(raw,AcDb::kForRead)!=Acad::eOk) throw std::runtime_error("block table");
    Read<AcDbBlockTable> table(raw); AcDbBlockTableIterator* it=nullptr;
    if(table->newIterator(it)!=Acad::eOk) throw std::runtime_error("block iterator");
    std::unique_ptr<AcDbBlockTableIterator> iterator(it); bool first=true;
    out << '[';
    for(;!it->done();it->step()) {
        AcDbBlockTableRecord* r=nullptr;
        if(it->getRecord(r,AcDb::kForRead)!=Acad::eOk) throw std::runtime_error("record");
        Read<AcDbBlockTableRecord> record(r);
        if(!r->isFromExternalReference()) continue;
        AcString name,path,resolved; r->getName(name); r->pathName(path);
        const auto findStatus=acdbHostApplicationServices()->findFile(resolved,path.kwszPtr(),db,AcDbHostApplicationServices::kXRefDrawing);
        auto* xdb=r->xrefDatabase();
        out << (first?"":",") << "{\"handle\":" << q(handle(r)) << ",\"name\":" << q(utf(name.kwszPtr()))
            << ",\"stored_path\":" << q(utf(path.kwszPtr())) << ",\"status\":" << int(r->xrefStatus())
            << ",\"native_find_file_status\":" << int(findStatus) << ",\"native_found_path\":" << q(utf(resolved.kwszPtr()))
            << ",\"unloaded\":" << (r->isUnloaded()?"true":"false")
            << ",\"overlay\":" << (r->isFromOverlayReference()?"true":"false")
            << ",\"database_present\":" << (xdb?"true":"false")
            << ",\"database_file\":" << q(filename(xdb))
            << ",\"database_units\":" << (xdb?int(xdb->insunits()):-1) << '}'; first=false;
    }
    out << ']';
}
struct Inventory {
    AcDbObjectIdArray parentIds;
    ga::xref::LineControls controls;
    ga::xref::AreaControls areas;
    ga::xref::WindowInventory window;
    std::vector<AcDbObjectId> active;
    std::map<std::string,unsigned> counts,layers,issues;
    std::set<std::string> targets{"7BF","16020","BB76"};
    std::ostringstream refs, found,clips;
    unsigned total=0,xrefs=0,targetCount=0,clipCount=0;
    Inventory() { refs<<std::setprecision(17); found<<std::setprecision(17); }
    void walk(AcDbObjectId id,const AcGeMatrix3d& transform,std::vector<std::string> ancestry,
              const std::string& context,const std::string& inheritedLayer="") {
        if(ancestry.size()>64 || total>1000000) throw std::runtime_error("bounded inventory exceeded");
        if(std::find(active.begin(),active.end(),id)!=active.end()) { ++issues["cyclic_record"]; return; }
        active.push_back(id);
        auto record=open<AcDbBlockTableRecord>(id);
        AcDbBlockTableRecordIterator* raw=nullptr;
        if(record->newIterator(raw)!=Acad::eOk || !raw) throw std::runtime_error("entity iterator");
        std::unique_ptr<AcDbBlockTableRecordIterator> it(raw);
        for(;!it->done();it->step()) {
            if(total>=1000000) throw std::runtime_error("bounded inventory exceeded");
            AcDbEntity* rawEntity=nullptr;
            if(it->getEntity(rawEntity,AcDb::kForRead)!=Acad::eOk || !rawEntity) { ++issues["unreadable_entity"]; continue; }
            Read<AcDbEntity> entity(rawEntity); ++total;
            const std::string h=handle(entity.get());
            AcString layer; entity->layer(layer);
            ++counts[context+":"+utf(entity->isA()->name())]; ++layers[context+":"+utf(layer.kwszPtr())];
            std::string route; for(const auto& part:ancestry) route+=part+"/"; route+=h;
            window.inspect(*entity,transform,route,context,inheritedLayer);
            controls.inspect(*entity,parentIds,transform,*acdbHostApplicationServices()->workingDatabase(),route,context);
            areas.inspect(*entity,parentIds,transform,*acdbHostApplicationServices()->workingDatabase(),route,context);
            if(targets.count(h) && targetCount<100) {
                found << (targetCount++?",":"") << "{\"handle\":" << q(h) << ",\"type\":" << q(utf(entity->isA()->name()))
                    << ",\"layer\":" << q(utf(layer.kwszPtr())) << ",\"database_file\":" << q(filename(entity->database()))
                    << ",\"owner_record\":" << q(handle(entity->ownerId())) << ",\"chain\":";
                chain(found,ancestry); found << ",\"world_transform\":"; matrix(found,transform); found << '}';
            }
            auto* ref=AcDbBlockReference::cast(entity.get());
            if(!ref) continue;
            if(!ref->extensionDictionary().isNull()) {
                clips<<(clipCount++?",":"")<<"{\"route\":"<<q(route)<<",\"state\":";
                ga::xref::clipState(clips,*ref); clips<<'}';
            }
            if(AcDbMInsertBlock::cast(ref)) { ++issues["minsert_not_expanded"]; continue; }
            auto child=open<AcDbBlockTableRecord>(ref->blockTableRecord());
            auto next=ancestry; next.push_back(h);
            const auto world=transform*ref->blockTransform();
            std::string nextContext=context;
            if(child->isFromExternalReference()) {
                AcString name; child->getName(name); nextContext=utf(name.kwszPtr());
                refs << (xrefs++?",":"") << "{\"handle\":" << q(h) << ",\"name\":" << q(nextContext)
                    << ",\"record\":" << q(handle(child.get())) << ",\"status\":" << int(child->xrefStatus())
                    << ",\"database_present\":" << (child->xrefDatabase()?"true":"false") << ",\"chain\":";
                chain(refs,next); refs << ",\"world_transform\":"; matrix(refs,world);
                refs<<",\"visibility\":"; ga::xref::clipState(refs,*ref); refs << '}';
                if(child->xrefStatus()!=AcDb::kXrfResolved || !child->xrefDatabase()) {
                    ++issues["xref_status_"+std::to_string(int(child->xrefStatus()))]; continue;
                }
            }
            parentIds.append(ref->objectId());
            const std::string sourceLayer=layer.utf8Str();
            const auto separator=sourceLayer.find_last_of('|');
            const auto leaf=separator==std::string::npos?sourceLayer:sourceLayer.substr(separator+1);
            const auto effectiveLayer=leaf=="0"&&!inheritedLayer.empty()?inheritedLayer:sourceLayer;
            walk(ref->blockTableRecord(),world,next,nextContext,effectiveLayer);
            parentIds.removeLast();
        }
        active.pop_back();
    }
    void write(std::ostream& out) {
        out << "{\"visited_instances\":" << total << ",\"xref_instances\":[" << refs.str()
            << "],\"target_instances\":[" << found.str() << "]"
            << ",\"block_clip_states\":["<<clips.str()<<"]"
            << ",\"native_line_controls\":["<<controls.rows.str()<<"]";
        out<<",\"native_area_controls\":"; areas.finish(out);
        if(window.enabled) { out<<",\"window_inventory\":"; window.write(out); }
        for(const auto& section : {std::make_pair("entity_counts",&counts),std::make_pair("layer_counts",&layers),std::make_pair("issues",&issues)}) {
            out << ',' << q(section.first) << ":{"; bool first=true;
            for(const auto& row:*section.second) { out << (first?"":",") << q(row.first) << ':' << row.second; first=false; }
            out << '}';
        }
        out << '}';
    }
};
void run() {
    ACHAR path[4096]={}; if(acedGetString(1,_T("\nXREF ledger request: "),path)!=RTNORM) return;
    try {
        std::ifstream in(utf(path)); std::string output;
        if(!std::getline(in,output)||output.empty()||std::ifstream(output).good()) throw std::runtime_error("output must be new");
        auto* db=acdbHostApplicationServices()->workingDatabase();
        const auto start=std::chrono::steady_clock::now();
        std::ostringstream out; out<<std::setprecision(17);
        out << "{\"schema\":\"green-atlas.native-xref-ledger/1\",\"source\":" << q(filename(db))
            << ",\"units\":" << int(db->insunits()) << ",\"graph\":"; graph(out,db);
        out << ",\"records\":"; records(out,db);
        Inventory inventory;
        const auto modePosition=in.tellg(); std::string mode; std::getline(in,mode);
        if(mode=="window") inventory.window.read(in);
        else { in.clear(); in.seekg(modePosition); inventory.areas.read(in); }
        inventory.areas.progressPath=output+".area-progress.json";
        if(inventory.areas.enabled||inventory.window.queries) {
            acrxLoadModule(_T("AcGeomentObj.dbx"),0); acrxDynamicLinker->loadModule(_T("AcBr.dbx"),1);
        }
        inventory.walk(acdbSymUtil()->blockModelSpaceId(db),AcGeMatrix3d(),{},"host");
        out << ",\"inventory\":"; inventory.write(out);
        out << ",\"elapsed_ms\":" << std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count() << '}';
        if(!ga::bridge::writeAtomicText(output,out.str())) throw std::runtime_error("ledger write failed");
        acutPrintf(_T("\nXREF ledger written"));
    } catch(const std::exception& e) { acutPrintf(_T("\nXREF ledger failed: %s"),AcString(e.what()).kwszPtr()); }
}
}
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode message,void* appId) {
    if(message==AcRx::kInitAppMsg) {
        acrxDynamicLinker->unlockApplication(appId); acrxDynamicLinker->registerAppMDIAware(appId);
        acedRegCmds->addCommand(_T("GA_XREF_LEDGER"),_T("GAXREFLEDGER"),_T("GAXREFLEDGER"),ACRX_CMD_MODAL,run);
        acedRegCmds->addCommand(_T("GA_XREF_LEDGER"),_T("GAXREFPATHS"),_T("GAXREFPATHS"),ACRX_CMD_MODAL,gaXrefPaths);
    } else if(message==AcRx::kUnloadAppMsg) acedRegCmds->removeGroup(_T("GA_XREF_LEDGER"));
    return AcRx::kRetOK;
}
