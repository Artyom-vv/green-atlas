#include "planar_faces_internal.h"
#include "dbents.h"
#include <algorithm>
#include <numeric>

namespace ga::faces {
std::vector<Connector> connect(std::vector<Input>& inputs,double gap,
                               const std::function<bool()>& cancelled) {
    struct End { AcGePoint3d point; std::size_t input; };
    std::vector<End> ends;
    for(std::size_t i=0;i<inputs.size();++i) {
        if(inputs[i].clipping) continue;
        AcGePoint3d a,b;
        require(inputs[i].curve->getStartPoint(a),"repair start");
        require(inputs[i].curve->getEndPoint(b),"repair end");
        ends.push_back({a,i}); ends.push_back({b,i});
    }
    std::sort(ends.begin(),ends.end(),[](const auto& a,const auto& b) {return a.point.x<b.point.x;});
    std::vector<std::size_t> order(inputs.size());
    std::iota(order.begin(),order.end(),0);
    std::sort(order.begin(),order.end(),[&](auto a,auto b) {return inputs[a].bounds.minPoint().x<inputs[b].bounds.minPoint().x;});
    std::vector<std::size_t> active;
    std::size_t next=0;
    std::vector<Connector> repairs;
    for(const auto& end:ends) {
        checkCancelled(cancelled);
        while(next<order.size()&&inputs[order[next]].bounds.minPoint().x<=end.point.x+gap)
            active.push_back(order[next++]);
        active.erase(std::remove_if(active.begin(),active.end(),[&](auto i) {
            return inputs[i].bounds.maxPoint().x<end.point.x-gap;
        }),active.end());
        bool connected=false,ambiguous=false,found=false;
        AcGePoint3d chosen;
        std::size_t target=0;
        for(auto i:active) {
            if(i==end.input) continue;
            const auto& candidate=inputs[i];
            if(end.point.y<candidate.bounds.minPoint().y-gap||end.point.y>candidate.bounds.maxPoint().y+gap
                ||std::abs(end.point.z-candidate.bounds.minPoint().z)>kEquality) continue;
            AcGePoint3d nearest;
            if(candidate.curve->getClosestPointTo(end.point,nearest,false)!=Acad::eOk) continue;
            const auto distance=end.point.distanceTo(nearest);
            if(distance<=kEquality) {connected=true;break;}
            if(distance>gap) continue;
            if(found&&chosen.distanceTo(nearest)>kEquality) ambiguous=true;
            else {found=true;chosen=nearest;target=i;}
        }
        // Existing incidence wins. Different nearby destinations are a genuine
        // choice, not permission to connect everything inside a radius.
        if(connected||ambiguous||!found) continue;
        bool duplicate=false;
        for(const auto& repair:repairs)
            if((repair.a.distanceTo(end.point)<=kEquality&&repair.b.distanceTo(chosen)<=kEquality)
                ||(repair.b.distanceTo(end.point)<=kEquality&&repair.a.distanceTo(chosen)<=kEquality)) duplicate=true;
        if(!duplicate) repairs.push_back({*inputs[end.input].routes.begin(),
            *inputs[target].routes.begin(),end.point,chosen});
    }
    for(const auto& repair:repairs)
        inputs.push_back(input(std::make_unique<AcDbLine>(repair.a,repair.b),
            {repair.from,repair.to},false,true));
    return repairs;
}
}
