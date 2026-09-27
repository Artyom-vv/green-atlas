#pragma once
#include "native_area_group.h"
#include "dbcurve.h"
#include <functional>
#include <set>

namespace ga::faces {
// World-coordinate clones only. Authored entities are never modified.
inline constexpr double kRepairGapMetres = 0.02;
inline constexpr const char* kPolicy = "native-planar-faces/1";
struct Source {
    std::string route, layer;
    std::unique_ptr<AcDbCurve> curve;
    bool clipping = false;
    bool needsArea = true;
};
struct Connector {
    std::string from, to;
    AcGePoint3d a, b;
};
struct Fragment {
    std::unique_ptr<AcDbCurve> curve;
    std::set<std::string> routes;
    bool clipping = false;
    bool repair = false;
};
struct Face {
    std::vector<Fragment> fragments;
    std::vector<Connector> repairs;
    std::unique_ptr<ga::nativeQuery::AreaGroupQuery> query;
    // Polyline export with an explicit maximum circular-arc chord deviation.
    // Native queries remain exact; local consumers must honour this error bound.
    std::vector<AcGePoint3d> display;
    double samplingToleranceUnits = 0;
    std::set<std::string> routes, physicalRoutes;
};
struct Result {
    std::vector<Face> faces;
    std::vector<std::string> issues;
};
Result assemble(std::vector<Source> sources, double unitsPerMetre,
                const std::function<bool()>& cancelled = {});
}
