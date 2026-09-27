#pragma once
// Isolated research: planar distances to real CAD curves, no sampled polygons.
#include "direct_query_kernel.h"
#include "dbcurve.h"
#include <ostream>

namespace ga::zone {
struct CurveAnswer {
    int status = -1;
    double distance = 0;
    AcGePoint3d nearest;
};
struct Policy {
    double buildingClearance, utilityClearance, siteClearance, spacing;
};
struct Verdict {
    std::string result = "unknown", reason;
    ga::direct::Answer building, site;
    CurveAnswer utility;
};
AcDbCurve* openCurve(AcDbDatabase& db, const std::string& handle, ga::direct::ReadEntities& owner);
CurveAnswer planarDistance(const AcDbCurve& curve, const AcGePoint3d& point);
Verdict evaluate(const ga::direct::Prepared& building, const ga::direct::Prepared& site,
                 const AcDbCurve& utility, const Policy& policy, const AcGePoint3d& point);
std::string quote(const std::string& text);
void xyz(std::ostream& out, const AcGePoint3d& point);
void emit(std::ostream& out, const AcGePoint3d& point, const Verdict& value);
void inventory(AcDbDatabase& db, const AcGePoint3d& point, std::ostream& out);
}
