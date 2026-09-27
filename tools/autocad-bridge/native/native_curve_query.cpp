#include "native_curve_query.h"
#include "AcString.h"
#include <cmath>
#include <stdexcept>

namespace ga::nativeQuery {
namespace {
bool finite(const AcGePoint3d& p) {
    return std::isfinite(p.x) && std::isfinite(p.y) && std::isfinite(p.z);
}
}
void CurveQuery::prepare(AcDbEntity& entity, const AcGeMatrix3d& transform) {
    curve = nullptr;
    owner.reset();
    for(int i=0;i<4;++i) for(int j=0;j<4;++j)
        if(!std::isfinite(transform(i,j))) throw std::runtime_error("nonfinite curve transform");
    const std::string type = AcString(entity.isA()->name()).utf8Str();
    // Complex 2d/3dPolyline containers cannot be safely shallow-cloned.
    const bool supported = type == "AcDbLine" || type == "AcDbPolyline"
        || type == "AcDbArc" || type == "AcDbCircle" || type == "AcDbEllipse"
        || type == "AcDbSpline";
    AcDbEntity* copy = nullptr;
    const auto status = supported ? entity.getTransformedCopy(transform, copy) : Acad::eNotApplicable;
    owner.reset(copy);
    if(status != Acad::eOk || !AcDbCurve::cast(copy))
        throw std::runtime_error("native world curve status " + std::to_string(int(status)));
    curve = AcDbCurve::cast(copy);
}
ga::direct::Answer CurveQuery::queryPlanar(const AcGePoint3d& point) const {
    if(!curve || !finite(point)) throw std::runtime_error("native curve not ready or nonfinite point");
    ga::direct::Answer answer;
    AcGePoint3d nearest;
    const auto status = curve->getClosestPointTo(point, AcGeVector3d::kZAxis, nearest, false);
    answer.status = int(status);
    if(status == Acad::eOk && finite(nearest)) {
        answer.distance = std::hypot(point.x-nearest.x, point.y-nearest.y);
        answer.distanceComplete = std::isfinite(answer.distance);
        answer.nearest = nearest;
    }
    return answer;
}
}
