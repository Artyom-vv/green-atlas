#pragma once
#include "geometry_types.h"
#include "acdb.h"
#include "gept3dar.h"
class AcDbObject;
class AcDbEntity;

namespace ga::bridge {

std::string utf8(const ACHAR* value);
std::string objectHandle(AcDbObject* object);
std::string entityHandle(AcDbEntity* entity);
bool isNonCalculationContext(const AcDbEntity* entity);
double unitToMetres(AcDb::UnitsValue units);
Point3 point3(const AcGePoint3d& point);

}  // namespace ga::bridge
