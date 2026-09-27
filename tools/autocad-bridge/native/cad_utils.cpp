#include "cad_utils.h"
#include "AcString.h"
#include "dbdim.h"
#include <algorithm>

namespace ga::bridge {

std::string utf8(const ACHAR* value) {
    return value == nullptr ? std::string() : AcString(value).utf8Str();
}

std::string objectHandle(AcDbObject* object) {
    AcDbHandle handle;
    object->getAcDbHandle(handle);
    ACHAR buffer[AcDbHandle::kStrSiz] = {};
    return handle.getIntoAsciiBuffer(buffer) ? utf8(buffer) : std::string();
}

std::string entityHandle(AcDbEntity* entity) {
    return objectHandle(entity);
}

bool isNonCalculationContext(const AcDbEntity* entity) {
    if (entity == nullptr) return false;
    if (entity->isKindOf(AcDbDimension::desc())) {
        return true;
    }
    const std::string entityType = utf8(entity->isA()->name());
    static const char* const types[] = {
        "AcDbAttribute",
        "AcDbAttributeDefinition",
        "AcDbDimension",
        "AcDbFcf",
        "AcDbGeoPositionMarker",
        "AcDbImage",
        "AcDbMLeader",
        "AcDbMText",
        "AcDbOle2Frame",
        "AcDbRasterImage",
        "AcDbTable",
        "AcDbText",
        "AcDbUnderlayReference",
        "AcDbViewport",
        "AcDbWipeout",
    };
    return std::find_if(
               std::begin(types), std::end(types),
               [&entityType](const char* value) { return entityType == value; }) !=
        std::end(types);
}

double unitToMetres(const AcDb::UnitsValue units) {
    switch (units) {
    case AcDb::kUnitsInches: return 0.0254;
    case AcDb::kUnitsFeet: return 0.3048;
    case AcDb::kUnitsMiles: return 1609.344;
    case AcDb::kUnitsMillimeters: return 0.001;
    case AcDb::kUnitsCentimeters: return 0.01;
    case AcDb::kUnitsMeters: return 1.0;
    case AcDb::kUnitsKilometers: return 1000.0;
    case AcDb::kUnitsMicroinches: return 0.0000000254;
    case AcDb::kUnitsMils: return 0.0000254;
    case AcDb::kUnitsYards: return 0.9144;
    case AcDb::kUnitsAngstroms: return 0.0000000001;
    case AcDb::kUnitsNanometers: return 0.000000001;
    case AcDb::kUnitsMicrons: return 0.000001;
    case AcDb::kUnitsDecimeters: return 0.1;
    case AcDb::kUnitsDekameters: return 10.0;
    case AcDb::kUnitsHectometers: return 100.0;
    case AcDb::kUnitsGigameters: return 1000000000.0;
    case AcDb::kUnitsAstronomical: return 149597870700.0;
    case AcDb::kUnitsLightYears: return 9460730472580800.0;
    case AcDb::kUnitsParsecs: return 30856775814913672.0;
    case AcDb::kUnitsUSSurveyFeet: return 1200.0 / 3937.0;
    case AcDb::kUnitsUSSurveyInch: return 100.0 / 3937.0;
    case AcDb::kUnitsUSSurveyYard: return 3600.0 / 3937.0;
    case AcDb::kUnitsUSSurveyMile: return 6336000.0 / 3937.0;
    default: return 0.0;
    }
}

Point3 point3(const AcGePoint3d& point) {
    return {point.x, point.y, point.z};
}

}  // namespace ga::bridge
