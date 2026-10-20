#pragma once

// Called only while generating the diagnostic seed, never on user inputs.
namespace {
void seedInstanceContract(const fs::path& root) {
    AcDbDatabase db(false, true);
    const auto path = root / "source/host.dwg";
    ok(db.readDwgFile(AcString(path.string().c_str()).kwszPtr()), "contract fixture read");
    ok(db.closeInput(true), "contract fixture close input");
    AcDbObjectId innerId, outerId;
    auto table = open<AcDbBlockTable>(db.blockTableId(), AcDb::kForWrite);
    {
        auto* inner = new AcDbBlockTableRecord();
        ok(inner->setName(L"GA_CONTRACT_INNER"), "contract inner name");
        ok(table->add(innerId, inner), "contract inner record");
        auto shape = std::make_unique<AcDbPolyline>();
        shape->setDatabaseDefaults(&db);
        for (const auto& vertex : std::vector<AcGePoint2d>{{0,0},{1,0},{1,1},{0,1}})
            ok(shape->addVertexAt(shape->numVerts(), vertex), "contract vertex");
        shape->setClosed(true);
        if (!shape->isClosed()) throw std::runtime_error("contract fixture closure failed");
        AcDbObjectId shapeId;
        ok(inner->appendAcDbEntity(shapeId, shape.get()), "contract layer zero shape");
        shape.release()->close();
        inner->close();
    }
    {
        auto* outer = new AcDbBlockTableRecord();
        ok(outer->setName(L"GA_CONTRACT_OUTER"), "contract outer name");
        ok(table->add(outerId, outer), "contract outer record");
        auto reference = std::make_unique<AcDbBlockReference>(AcGePoint3d(0.5,0.5,0), innerId);
        reference->setDatabaseDefaults(&db);
        ok(reference->setRotation(0.15), "contract nested rotation");
        AcDbObjectId referenceId;
        ok(outer->appendAcDbEntity(referenceId, reference.get()), "contract inner insertion");
        auto attribute = std::make_unique<AcDbAttribute>();
        attribute->setDatabaseDefaults(&db);
        ok(attribute->setPosition(AcGePoint3d(0.25,0.5,0)), "contract attribute position");
        ok(attribute->setHeight(0.1), "contract attribute height");
        ok(attribute->setTag(L"GA_ANNOTATION"), "contract attribute tag");
        ok(attribute->setTextString(L"PRESERVE_THIS_VALUE"), "contract attribute value");
        auto blockTransform = reference->blockTransform();
        ok(attribute->setAttributeFromBlock(blockTransform), "contract attribute transform");
        AcDbObjectId attributeId;
        ok(reference->appendAttribute(attributeId, attribute.get()), "contract attach annotation");
        attribute.release()->close();
        reference.release()->close();
        outer->close();
    }
    table.reset();
    auto reference = std::make_unique<AcDbBlockReference>(AcGePoint3d(3,3,0), outerId);
    reference->setDatabaseDefaults(&db);
    ok(reference->setLayer(L"BUILDING"), "contract inherited layer");
    ok(reference->setRotation(0.3), "contract outer rotation");
    ok(reference->setScaleFactors(AcGeScale3d(0.8,0.8,1)), "contract outer scale");
    append(&db, reference.release());
    ok(db.saveAs(AcString(path.string().c_str()).kwszPtr(), false, AcDb::kDHL_CURRENT), "save owned contract fixture");
}
}
