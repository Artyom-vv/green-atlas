#pragma once
#include "gepnt3d.h"
#include <iosfwd>
#include <vector>
class AcBrBrep;
void emitDistanceProbe(std::ostream&, const AcBrBrep&, const std::vector<AcGePoint3d>&);
