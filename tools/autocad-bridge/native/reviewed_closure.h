#pragma once
#include "dbpl.h"
#include "dbidmap.h"
#include "dbsymtb.h"
#include <memory>
#include <stdexcept>

namespace ga::nativeQuery {
// Same native construction as the capture's near-closed proposal. This command
// is requested ONLY for the exact path approved against that immutable capture.
// Original objects stay read-open; all edits are in a disposable scratch DB.
class ReviewedClosure {
    std::unique_ptr<AcDbDatabase> scratch_;
    AcDbPolyline* read_ = nullptr;
public:
    ~ReviewedClosure() { if(read_) read_->close(); }
    AcDbPolyline& prepare(AcDbEntity& source) {
        auto* line = AcDbPolyline::cast(&source);
        if(!line || line->isClosed() || line->numVerts()<4 || !source.database())
            throw std::runtime_error("reviewed closure requires an open database polyline");
        scratch_ = std::make_unique<AcDbDatabase>(true, true);
        AcDbBlockTable* table = nullptr;
        auto status = scratch_->getBlockTable(table, AcDb::kForRead);
        AcDbObjectId modelId;
        if(status == Acad::eOk && table) {
            status = table->getAt(ACDB_MODEL_SPACE, modelId);
            table->close();
        }
        if(status != Acad::eOk || modelId.isNull())
            throw std::runtime_error("reviewed closure scratch model unavailable");
        AcDbObjectIdArray ids; ids.append(source.objectId());
        AcDbIdMapping mapping;
        status = source.database()->wblockCloneObjects(ids, modelId, mapping, AcDb::kDrcIgnore);
        AcDbIdPair pair; pair.setKey(source.objectId());
        if(status != Acad::eOk || !mapping.compute(pair) || pair.value().isNull())
            throw std::runtime_error("reviewed closure scratch clone failed");
        AcDbPolyline* write = nullptr;
        status = acdbOpenObject(write, pair.value(), AcDb::kForWrite);
        if(status != Acad::eOk || !write)
            throw std::runtime_error("reviewed closure clone write-open failed");
        write->setClosed(Adesk::kTrue);
        const bool closed = write->isClosed();
        write->close();
        if(!closed) throw std::runtime_error("reviewed closure did not close");
        status = acdbOpenObject(read_, pair.value(), AcDb::kForRead);
        if(status != Acad::eOk || !read_)
            throw std::runtime_error("reviewed closure clone read-open failed");
        return *read_;
    }
};
}
