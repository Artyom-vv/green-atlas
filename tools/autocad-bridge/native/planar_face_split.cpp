#include "planar_faces_internal.h"
#include <algorithm>
#include <numeric>

namespace ga::faces {
namespace {
void cut(Input& value,const AcGePoint3d& point) {
    AcGePoint3d nearest;
    if(value.curve->getClosestPointTo(point,nearest,false)!=Acad::eOk
        ||point.distanceTo(nearest)>kEquality) return;
    double parameter=0;
    if(value.curve->getParamAtPoint(nearest,parameter)!=Acad::eOk) return;
    // Do not confuse drawing-unit tolerance with a curve's parameter units.
    AcGePoint3d start,end;
    require(value.curve->getStartPoint(start),"split start");
    require(value.curve->getEndPoint(end),"split end");
    if(nearest.distanceTo(start)<=kEquality||nearest.distanceTo(end)<=kEquality) return;
    if(parameter<=value.first||parameter>=value.last) return;
    for(const auto previous:value.cuts) {
        AcGePoint3d known;
        require(value.curve->getPointAtParam(previous,known),"previous split");
        if(known.distanceTo(nearest)<=kEquality) return;
    }
    value.cuts.push_back(parameter);
}
}
Graph intersectAndSplit(std::vector<Input>& inputs, Result& result,
                        const std::function<bool()>& cancelled) {
    // Sweep extents before native intersection. No all-pairs street scan.
    std::vector<std::size_t> order(inputs.size());
    std::iota(order.begin(),order.end(),0);
    std::sort(order.begin(),order.end(),[&](auto a,auto b) {
        return inputs[a].bounds.minPoint().x<inputs[b].bounds.minPoint().x;
    });
    for(std::size_t a=0;a<order.size();++a) {
        checkCancelled(cancelled);
        auto& left=inputs[order[a]];
        for(std::size_t b=a+1;b<order.size();++b) {
            auto& right=inputs[order[b]];
            if(right.bounds.minPoint().x>left.bounds.maxPoint().x+kEquality) break;
            if(right.bounds.minPoint().y>left.bounds.maxPoint().y+kEquality
                ||right.bounds.maxPoint().y<left.bounds.minPoint().y-kEquality
                ||std::abs(left.bounds.minPoint().z-right.bounds.minPoint().z)>kEquality) continue;
            AcGePoint3dArray hits;
            const auto status=left.curve->intersectWith(right.curve.get(),AcDb::kOnBothOperands,hits);
            if(status!=Acad::eOk) {
                result.issues.push_back(*left.routes.begin()+": native intersection:"+std::to_string(int(status)));
                continue;
            }
            for(const auto& point:hits) { cut(left,point); cut(right,point); }
            for(auto* value:{&left,&right}) {
                auto& other=value==&left?right:left;
                AcGePoint3d start,end;
                require(value->curve->getStartPoint(start),"incidence start");
                require(value->curve->getEndPoint(end),"incidence end");
                cut(other,start); cut(other,end);
            }
        }
    }
    Graph graph;
    for(auto& value:inputs) {
        checkCancelled(cancelled);
        try {
            if(value.cuts.empty()) {
                graph.add({std::move(value.curve),value.routes,value.clipping,value.repair});
                continue;
            }
            std::sort(value.cuts.begin(),value.cuts.end());
            AcGeDoubleArray parameters;
            for(auto parameter:value.cuts) parameters.append(parameter);
            AcDbVoidPtrArray parts;
            const auto status=value.curve->getSplitCurves(parameters,parts);
            std::vector<std::unique_ptr<AcDbEntity>> owned;
            for(void* raw:parts) owned.emplace_back(static_cast<AcDbEntity*>(raw));
            require(status,"native face split");
            for(auto& entity:owned) {
                auto* curve=AcDbCurve::cast(entity.get());
                if(!curve) throw std::runtime_error("native split returned noncurve");
                entity.release();
                graph.add({std::unique_ptr<AcDbCurve>(curve),value.routes,value.clipping,value.repair});
            }
        } catch(const std::exception& error) {
            result.issues.push_back(*value.routes.begin()+": "+error.what());
        }
    }
    return graph;
}
}
