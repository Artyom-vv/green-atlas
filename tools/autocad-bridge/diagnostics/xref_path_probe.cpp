#include "xref_path_probe.h"
#include "file_io.h"
#include "aced.h"
#include "adslib.h"
#include "dbapserv.h"
#include "dbsymtb.h"
#include "acdbxref.h"
#include "AcString.h"
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace {
std::string q(const std::string& s) { return "\""+ga::bridge::jsonEscape(s)+"\""; }
std::string text(const AcString& s) { return s.utf8Str(); }
}
void gaXrefPaths() {
    ACHAR request[4096]={}; if(acedGetString(1,_T("\nExplicit XREF path map: "),request)!=RTNORM) return;
    std::string output;
    try {
        std::ifstream in(text(AcString(request))); std::string count;
        if(!std::getline(in,output)||std::ifstream(output).good()||!std::getline(in,count))
            throw std::runtime_error("new report and explicit map required");
        const auto size=std::stoi(count); if(size<1||size>100) throw std::runtime_error("bounded map size");
        auto* db=acdbHostApplicationServices()->workingDatabase();
        AcDbObjectIdArray reload;
        std::ostringstream report; report<<"{\"scope\":\"explicit staged paths only; no save\",\"mappings\":[";
        for(int i=0;i<size;++i) {
            std::string handle,name,path;
            if(!std::getline(in,handle)||!std::getline(in,name)||!std::getline(in,path))
                throw std::runtime_error("incomplete explicit path map");
            if(!std::ifstream(path).good()) throw std::runtime_error("mapped file unavailable");
            AcDbObjectId id;
            auto status=db->getAcDbObjectId(id,false,AcDbHandle(AcString(handle.c_str()).kwszPtr()));
            AcDbBlockTableRecord* record=nullptr;
            if(status==Acad::eOk) status=acdbOpenObject(record,id,AcDb::kForWrite);
            if(status!=Acad::eOk||!record) throw std::runtime_error("xref record write-open status "+std::to_string(int(status)));
            AcString actualName,oldPath; record->getName(actualName); record->pathName(oldPath);
            if(!record->isFromExternalReference()||text(actualName)!=name) {
                record->close(); throw std::runtime_error("explicit XREF identity mismatch");
            }
            const auto before=record->xrefStatus();
            status=record->setPathName(AcString(path.c_str()).kwszPtr());
            record->close();
            report<<(i?",":"")<<"{\"record\":"<<q(handle)<<",\"name\":"<<q(name)
                <<",\"before\":"<<int(before)<<",\"old_path\":"<<q(text(oldPath))
                <<",\"new_path\":"<<q(path)<<",\"set_path_status\":"<<int(status)<<'}';
            if(status==Acad::eOk) reload.append(id);
        }
        const auto status=acdbReloadXrefs(db,reload,true);
        report<<"],\"reload_status\":"<<int(status)<<",\"reload_count\":"<<reload.length()<<'}';
        if(!ga::bridge::writeAtomicText(output,report.str())) throw std::runtime_error("path report write failed");
        acutPrintf(_T("\nNative XREF reload completed, status %d"),int(status));
    } catch(const std::exception& e) {
        if(!output.empty()&&!std::ifstream(output).good()) ga::bridge::writeAtomicText(output,"{\"error\":"+q(e.what())+"}");
        acutPrintf(_T("\nXREF path experiment failed: %s"),AcString(e.what()).kwszPtr());
    }
}
