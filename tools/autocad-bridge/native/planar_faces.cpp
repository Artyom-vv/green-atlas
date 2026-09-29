#include "planar_faces_internal.h"
#include "dbpl.h"
#include "dbents.h"
#include <cmath>
#include <stdexcept>

namespace ga::faces {
void require(Acad::ErrorStatus status, const char* operation) {
    if(status != Acad::eOk)
        throw std::runtime_error(std::string(operation)+":"+std::to_string(int(status)));
}
void checkCancelled(const std::function<bool()>& cancelled) {
    if(cancelled && cancelled()) throw std::runtime_error("native face preparation cancelled");
}
Input input(std::unique_ptr<AcDbCurve> curve, const std::set<std::string>& routes,
            bool clipping, bool repair) {
    Input result;
    result.curve=std::move(curve); result.routes=routes;
    result.clipping=clipping; result.repair=repair;
    require(result.curve->getStartParam(result.first),"face start parameter");
    require(result.curve->getEndParam(result.last),"face end parameter");
    require(result.curve->getGeomExtents(result.bounds),"face bounds");
    const auto a=result.bounds.minPoint(),b=result.bounds.maxPoint();
    if(!std::isfinite(a.x)||!std::isfinite(a.y)||!std::isfinite(a.z)
        ||!std::isfinite(b.x)||!std::isfinite(b.y)||!std::isfinite(b.z)
        ||std::abs(a.z-b.z)>kEquality)
        throw std::runtime_error("face curve is not finite and horizontal");
    return result;
}
std::vector<Input> primitives(std::vector<Source>& sources, Result& result,
                              const std::function<bool()>& cancelled) {
    std::vector<Input> expanded;
    for(auto& source:sources) {
        checkCancelled(cancelled);
        try {
            if(AcDbPolyline::cast(source.curve.get())) {
                AcDbVoidPtrArray parts;
                const auto status=source.curve->explode(parts);
                // Own every returned object even when AutoCAD reports an error.
                std::vector<std::unique_ptr<AcDbEntity>> owned;
                for(void* raw:parts) owned.emplace_back(static_cast<AcDbEntity*>(raw));
                require(status,"face native explode");
                for(auto& entity:owned) {
                    auto* curve=AcDbCurve::cast(entity.get());
                    if(!curve) throw std::runtime_error("face explode returned noncurve");
                    entity.release();
                    expanded.push_back(input(std::unique_ptr<AcDbCurve>(curve),
                        {source.route},source.clipping));
                }
            } else if(AcDbLine::cast(source.curve.get())||AcDbArc::cast(source.curve.get())) {
                expanded.push_back(input(std::move(source.curve),{source.route},source.clipping));
            } else {
                // Exact closed circles/ellipses/splines retain their existing
                // native area query. Never approximate them to enable a face.
                result.issues.push_back(source.route+": curve retained without face splitting");
            }
        } catch(const std::exception& error) {
            checkCancelled(cancelled);
            result.issues.push_back(source.route+": "+error.what());
        }
    }
    return expanded;
}
Result assemble(std::vector<Source> sources, double unitsPerMetre,
                const std::function<bool()>& cancelled) {
    if(!std::isfinite(unitsPerMetre)||unitsPerMetre<=0)
        throw std::runtime_error("native face units unavailable");
    Result result;
    checkCancelled(cancelled);
    auto inputs=primitives(sources,result,cancelled);
    const auto repairs=connect(inputs,kRepairGapMetres*unitsPerMetre,cancelled);
    auto graph=intersectAndSplit(inputs,result,cancelled);
    for(const auto& source:sources) if(source.needsArea&&!source.clipping) graph.needsArea.insert(source.route);
    collectFaces(graph,repairs,result,cancelled);
    return result;
}
}
