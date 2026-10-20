#include "xref_instance_access.h"
#include "AcString.h"
#include "dbsymutl.h"
#include "dbsymtb.h"
#include "dbents.h"
#include "dbtable.h"
#include "AcDbCompoundObjectId.h"
#include <algorithm>
#include <cmath>
#include <sstream>
#include <stdexcept>

namespace ga::xref {
namespace {
constexpr double kMatrixAgreementTolerance=1e-8;

AcGeMatrix3d parentTransform(AcDbBlockReference& reference,double& maximumDelta) {
    AcGeMatrix3d native;
    const auto status=reference.getCompoundObjectTransform(native);
    if(status!=Acad::eOk)
        throw std::runtime_error("native parent instance transform unavailable: "+std::to_string(int(status)));
    const auto captured=reference.blockTransform();
    for(int i=0;i<4;++i) for(int j=0;j<4;++j) {
        if(!std::isfinite(native(i,j))||!std::isfinite(captured(i,j)))
            throw std::runtime_error("native parent instance transform is not finite");
        maximumDelta=std::max(maximumDelta,std::abs(native(i,j)-captured(i,j)));
    }
    if(maximumDelta>kMatrixAgreementTolerance)
        throw std::runtime_error("native parent transform differs from capture traversal");
    return native;
}
}
void resolve(AcDbDatabase& host,const std::string& route,Instance& result) {
    std::vector<std::string> tokens;
    std::istringstream stream(route); std::string token;
    while(std::getline(stream,token,'/')) tokens.push_back(token);
    if(tokens.empty()||tokens.size()>64||route.empty()||route.back()=='/')
        throw std::runtime_error("invalid instance route");
    for(const auto& value:tokens)
        if(value.empty()||value.size()>16||value.find_first_not_of("0123456789ABCDEF")!=std::string::npos)
            throw std::runtime_error("invalid instance handle");
    AcDbObjectId recordId=acdbSymUtil()->blockModelSpaceId(&host);
    bool annotationAncestor=false;
    for(std::size_t depth=0;depth<tokens.size();++depth) {
        AcDbBlockTableRecord* record=nullptr;
        if(acdbOpenObject(record,recordId,AcDb::kForRead)!=Acad::eOk || !record) throw std::runtime_error("route record unavailable");
        struct Close { AcDbBlockTableRecord* p; ~Close(){p->close();} } close{record};
        if(record->isFromExternalReference() &&
            (record->xrefStatus()!=AcDb::kXrfResolved || !record->xrefDatabase()))
            throw std::runtime_error("route XREF unavailable, native status "+std::to_string(int(record->xrefStatus())));
        AcDbEntity* matched=nullptr;
        AcDbObjectId candidate;
        const auto handle=AcDbHandle(AcString(tokens[depth].c_str()).kwszPtr());
        // A handle is database-local, NOT globally unique across XREFs. Verify
        // ownership before accepting it; retain native traversal for redirected
        // XREF records whose entity database differs from the record database.
        auto* entityDatabase=record->isFromExternalReference()?record->xrefDatabase():record->database();
        const auto expectedOwner=record->isFromExternalReference()
            ?acdbSymUtil()->blockModelSpaceId(entityDatabase):recordId;
        if(entityDatabase->getAcDbObjectId(candidate,false,handle)==Acad::eOk
            &&acdbOpenObject(matched,candidate,AcDb::kForRead)==Acad::eOk&&matched) {
            if(matched->ownerId()!=expectedOwner) {matched->close();matched=nullptr;}
        }
        if(!matched) {
            AcDbBlockTableRecordIterator* raw=nullptr;
            if(record->newIterator(raw)!=Acad::eOk || !raw) throw std::runtime_error("route iterator unavailable");
            std::unique_ptr<AcDbBlockTableRecordIterator> iterator(raw);
            for(;!iterator->done();iterator->step()) {
                AcDbEntity* entity=nullptr;
                if(iterator->getEntity(entity,AcDb::kForRead)!=Acad::eOk || !entity) throw std::runtime_error("route entity unreadable");
                AcDbHandle h; entity->getAcDbHandle(h); ACHAR text[32]={}; h.getIntoAsciiBuffer(text);
                if(tokens[depth]==AcString(text).utf8Str()) { matched=entity; break; }
                entity->close();
            }
        }
        if(!matched) throw std::runtime_error("instance handle not found in parent: "+tokens[depth]);
        result.opened.values.push_back(matched);
        if(depth+1==tokens.size()) { result.entity=matched; break; }
        auto* reference=AcDbBlockReference::cast(matched);
        const bool table=reference&&reference->isA()==AcDbTable::desc();
        if(!reference || (!table&&reference->isA()!=AcDbBlockReference::desc()))
            throw std::runtime_error("route parent not supported single block instance");
        annotationAncestor=annotationAncestor||table;
        result.parents.append(reference->objectId());
        if(!annotationAncestor)
            result.transform=result.transform*parentTransform(*reference,result.compoundMatrixDelta);
        recordId=reference->blockTableRecord();
    }
    // Таблица является оформлением, но все её дочерние адреса проверяются
    // по владельцам до leaf. Чужой handle не становится контекстом таблицы.
    if(annotationAncestor) throw NonCalculationContext();
    // Адрес уже проверен по владельцам и native-матрице каждого блока.
    // CompoundObjectId может отвергнуть адрес внутри динамического блока и
    // вернуть identity с eOk. Такая матрица не заменяет проверенный SDK-путь.
    AcDbCompoundObjectId compound;
    auto status=compound.set(result.entity->objectId(),result.parents,&host);
    if(status!=Acad::eOk || compound.status()!=AcDbCompoundObjectId::kValid) return;
    AcGeMatrix3d native;
    status=compound.getTransform(native);
    if(status!=Acad::eOk)
        throw std::runtime_error("native compound instance transform unavailable: "+std::to_string(int(status)));
    for(int i=0;i<4;++i) for(int j=0;j<4;++j) {
        if(!std::isfinite(native(i,j)))
            throw std::runtime_error("native compound instance transform is not finite");
        result.compoundMatrixDelta=std::max(result.compoundMatrixDelta,std::abs(native(i,j)-result.transform(i,j)));
    }
    if(result.compoundMatrixDelta>kMatrixAgreementTolerance)
        throw std::runtime_error("native compound transform differs from chain traversal");
}
}
