// Diagnostic ledger, not a calculation representation. Read-only native
// classes/handles make demand-loading differences visible before admission.
#pragma once
#include "dbsymtb.h"
#include "dbproxy.h"
#include "dbents.h"
#include "file_io.h"
#include <map>
#include <set>

namespace ga::diagnostic {
template<class T> struct CloseEntity { void operator()(T* p) const { if(p) p->close(); } };
template<class T> using ReadObject = std::unique_ptr<T, CloseEntity<T>>;
inline std::string json(const ACHAR* s) {
    return "\"" + ga::bridge::jsonEscape(s ? AcString(s).utf8Str() : "") + "\"";
}
inline std::string handle(AcDbObjectId id) {
    ACHAR value[32]{}; id.nonForwardedHandle().getIntoAsciiBuffer(value);
    return AcString(value).utf8Str();
}
inline void inventory(const std::filesystem::path& path, AcDbDatabase& db) {
    AcDbXrefGraph graph;
    if(acdbGetHostDwgXrefGraph(&db,graph,true)!=Acad::eOk)
        throw std::runtime_error("inventory graph failed");
    std::ofstream out(path);
    out << "{\"scope\":\"native block records and entity identities, not full geometry equality\",\"databases\":[";
    std::set<AcDbDatabase*> visited; bool firstDatabase=true;
    for(int n=0;n<graph.numNodes();++n) {
        auto* node=graph.xrefNode(n); auto* source=node->database();
        if(!source||!visited.insert(source).second) continue;
        out<<(firstDatabase?"":",")<<"{\"name\":"<<json(node->name())<<",\"entities\":[";
        firstDatabase=false; bool firstEntity=true;
        AcDbBlockTable* raw=nullptr;
        if(source->getBlockTable(raw,AcDb::kForRead)!=Acad::eOk)
            throw std::runtime_error("inventory block table failed");
        ReadObject<AcDbBlockTable> table(raw);
        AcDbBlockTableIterator* iterator=nullptr;
        if(table->newIterator(iterator)!=Acad::eOk) throw std::runtime_error("inventory table iterator failed");
        std::unique_ptr<AcDbBlockTableIterator> blocks(iterator);
        for(;!blocks->done();blocks->step()) {
            AcDbBlockTableRecord* block=nullptr;
            if(blocks->getRecord(block,AcDb::kForRead)!=Acad::eOk)
                throw std::runtime_error("inventory block open failed");
            ReadObject<AcDbBlockTableRecord> record(block);
            AcDbBlockTableRecordIterator* ei=nullptr;
            if(record->newIterator(ei)!=Acad::eOk) throw std::runtime_error("inventory entity iterator failed");
            std::unique_ptr<AcDbBlockTableRecordIterator> entities(ei);
            for(;!entities->done();entities->step()) {
                AcDbObjectId id; if(entities->getEntityId(id)!=Acad::eOk)
                    throw std::runtime_error("inventory entity identity failed");
                AcDbEntity* rawEntity=nullptr;
                const auto status=entities->getEntity(rawEntity,AcDb::kForRead);
                ReadObject<AcDbEntity> entity(rawEntity);
                out<<(firstEntity?"":",")<<"{\"block\":\""<<handle(record->objectId())
                   <<"\",\"handle\":\""<<handle(id)<<"\",\"status\":"<<int(status);
                firstEntity=false;
                if(status==Acad::eOk&&entity) {
                    out<<",\"class\":"<<json(entity->isA()->name())<<",\"dxf\":"<<json(entity->isA()->dxfName())
                       <<",\"layer_id\":\""<<handle(entity->layerId())<<"\"";
                    if(auto* proxy=AcDbProxyEntity::cast(entity.get()))
                        out<<",\"proxy_original_class\":"<<json(proxy->originalClassName());
                }
                out<<'}';
            }
        }
        out<<"]}";
    }
    out<<"]}";
    if(!out.good()) throw std::runtime_error("inventory publication failed");
}
}
