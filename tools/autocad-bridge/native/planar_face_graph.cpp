#include "planar_faces_internal.h"
#include "dbents.h"
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace ga::faces {
int Graph::node(const AcGePoint3d& point) {
    for(auto it=nodeIndex.lower_bound(point.x-kEquality);
        it!=nodeIndex.end()&&it->first<=point.x+kEquality;++it)
        if(nodes[it->second].distanceTo(point)<=kEquality) return it->second;
    const int index=int(nodes.size());
    nodes.push_back(point); outgoing.emplace_back(); nodeIndex.emplace(point.x,index);
    return index;
}
void Graph::add(Fragment fragment) {
    auto& curve=*fragment.curve;
    AcGePoint3d a,b;
    double first=0,last=0,d0=0,d1=0;
    require(curve.getStartPoint(a),"edge start"); require(curve.getEndPoint(b),"edge end");
    require(curve.getStartParam(first),"edge first parameter"); require(curve.getEndParam(last),"edge last parameter");
    require(curve.getDistAtParam(first,d0),"edge first distance"); require(curve.getDistAtParam(last,d1),"edge last distance");
    const double length=d1-d0;
    if(!std::isfinite(length)||length<=kEquality) return;
    const int ia=node(a),ib=node(b);
    if(AcDbLine::cast(&curve)) {
        for(const auto& incidence:outgoing[ia]) {
            auto& previous=edges[incidence.second/2];
            if(!AcDbLine::cast(previous.fragment.curve.get())||std::abs(previous.length-length)>kEquality) continue;
            if((previous.a==ia&&previous.b==ib)||(previous.a==ib&&previous.b==ia)) {
                previous.fragment.routes.insert(fragment.routes.begin(),fragment.routes.end());
                previous.fragment.clipping=previous.fragment.clipping&&fragment.clipping;
                return;
            }
        }
    }
    const auto origin=nodes.front();
    double integral=(a.x-origin.x)*(b.y-origin.y)-(b.x-origin.x)*(a.y-origin.y);
    if(auto* arc=AcDbArc::cast(&curve)) {
        if(std::abs(std::abs(arc->normal().z)-1)>kEquality)
            throw std::runtime_error("nonplanar native arc");
        const double angle=length/arc->radius()*(arc->normal().z>0?1:-1);
        integral+=arc->radius()*arc->radius()*(angle-std::sin(angle));
    } else if(!AcDbLine::cast(&curve)) throw std::runtime_error("unsupported native face primitive");
    AcGeVector3d da,db;
    require(curve.getFirstDeriv(first,da),"edge start tangent");
    require(curve.getFirstDeriv(last,db),"edge end tangent");
    const int index=int(edges.size());
    edges.push_back({std::move(fragment),ia,ib,length,integral/2});
    outgoing[ia].push_back({std::atan2(da.y,da.x),2*index});
    outgoing[ib].push_back({std::atan2(-db.y,-db.x),2*index+1});
}
namespace {
std::set<int> bridges(Graph& graph) {
    // Iterative DFS: an entire street cannot overflow the C++ call stack.
    struct Frame {int node,parentEdge;std::size_t next;};
    std::vector<int> discovery(graph.nodes.size(),-1),low(graph.nodes.size());
    std::set<int> result;
    int timer=0;
    for(int root=0;root<int(graph.nodes.size());++root) {
        if(discovery[root]>=0) continue;
        discovery[root]=low[root]=timer++;
        std::vector<Frame> stack{{root,-1,0}};
        while(!stack.empty()) {
            auto& frame=stack.back();
            if(frame.next==graph.outgoing[frame.node].size()) {
                const auto finished=frame; stack.pop_back();
                if(!stack.empty()) {
                    const int parent=stack.back().node;
                    low[parent]=std::min(low[parent],low[finished.node]);
                    if(low[finished.node]>discovery[parent]) result.insert(finished.parentEdge);
                }
                continue;
            }
            const int directed=graph.outgoing[frame.node][frame.next++].second,edge=directed/2;
            if(edge==frame.parentEdge) continue;
            const auto& value=graph.edges[edge];
            const int neighbor=directed%2?value.a:value.b;
            if(discovery[neighbor]>=0) low[frame.node]=std::min(low[frame.node],discovery[neighbor]);
            else {
                discovery[neighbor]=low[neighbor]=timer++;
                stack.push_back({neighbor,edge,0});
            }
        }
    }
    return result;
}
void displayEdge(Face& face,const Edge& edge,bool reverse) {
    // LINE/ARC are the only primitives admitted by the face graph. Record the
    // analytic sagitta, not a claimed zero tolerance for an arc approximation.
    constexpr double kMaxDisplayAngle=0.05;
    const auto* arc=AcDbArc::cast(edge.fragment.curve.get());
    const int samples=arc?std::max(1,int(std::ceil(edge.length/arc->radius()/kMaxDisplayAngle))):1;
    if(arc) {
        const double halfAngle=edge.length/arc->radius()/samples/2;
        face.samplingToleranceUnits=std::max(face.samplingToleranceUnits,
            2*arc->radius()*std::pow(std::sin(halfAngle/2),2));
    }
    double first=0,initial=0;
    require(edge.fragment.curve->getStartParam(first),"display first");
    require(edge.fragment.curve->getDistAtParam(first,initial),"display distance");
    for(int step=0;step<samples;++step) {
        AcGePoint3d point;
        const double fraction=double(step)/samples;
        require(edge.fragment.curve->getPointAtDist(initial+edge.length*(reverse?1-fraction:fraction),point),"display point");
        face.display.push_back(point);
    }
}
}
void collectFaces(Graph& graph,const std::vector<Connector>& repairs,
                  Result& result,const std::function<bool()>& cancelled) {
    const auto dangling=bridges(graph);
    for(auto& list:graph.outgoing) {
        list.erase(std::remove_if(list.begin(),list.end(),[&](const auto& entry) {
            return dangling.count(entry.second/2);
        }),list.end());
        std::sort(list.begin(),list.end());
    }
    std::vector<bool> visited(graph.edges.size()*2);
    for(int start=0;start<int(visited.size());++start) {
        checkCancelled(cancelled);
        if(visited[start]||dangling.count(start/2)) continue;
        int next=start;
        std::vector<int> directed;
        while(!visited[next]) {
            visited[next]=true; directed.push_back(next);
            const auto& edge=graph.edges[next/2];
            const auto& exits=graph.outgoing[next%2?edge.a:edge.b];
            const auto reverse=std::find_if(exits.begin(),exits.end(),[&](const auto& entry) {return entry.second==(next^1);});
            if(reverse==exits.end()) throw std::runtime_error("face reverse edge missing");
            next=exits[(std::size_t(reverse-exits.begin())+exits.size()-1)%exits.size()].second;
        }
        if(next!=start) continue;
        double orientedArea=0,physical=0,clipping=0;
        bool needsArea=false;
        std::set<int> unique;
        for(auto item:directed) {
            const auto& edge=graph.edges[item/2]; unique.insert(item/2);
            orientedArea+=edge.integral*(item%2?-1:1);
            (edge.fragment.clipping?clipping:physical)+=edge.length;
            for(const auto& route:edge.fragment.routes) if(graph.needsArea.count(route)) needsArea=true;
        }
        // Reject exterior walks, repeated boundaries and clipping-only faces.
        // A clipping line can close a physical outline, not define most of it.
        if(!needsArea||orientedArea<=kEquality*kEquality||unique.size()!=directed.size()
            ||physical<=clipping) continue;
        try {
            Face face;
            std::vector<AcDbEntity*> curves;
            for(auto item:directed) {
                const auto& edge=graph.edges[item/2];
                auto* copy=AcDbCurve::cast(edge.fragment.curve->clone());
                if(!copy) throw std::runtime_error("native fragment clone failed");
                Fragment fragment{std::unique_ptr<AcDbCurve>(copy),edge.fragment.routes,
                    edge.fragment.clipping,edge.fragment.repair};
                face.routes.insert(fragment.routes.begin(),fragment.routes.end());
                if(!fragment.clipping&&!fragment.repair)
                    face.physicalRoutes.insert(fragment.routes.begin(),fragment.routes.end());
                if(fragment.repair) for(const auto& repair:repairs)
                    if(fragment.routes.count(repair.from)&&fragment.routes.count(repair.to)) face.repairs.push_back(repair);
                curves.push_back(copy); face.fragments.push_back(std::move(fragment));
                displayEdge(face,edge,item%2);
            }
            if(face.physicalRoutes.empty()) continue;
            face.query=std::make_unique<ga::nativeQuery::AreaGroupQuery>();
            face.query->prepare(curves,AcGeMatrix3d::kIdentity);
            face.display.push_back(face.display.front());
            // Endpoints associated with one graph node may differ by twice
            // the equality tolerance. Include that join displacement too.
            face.samplingToleranceUnits += 2*kEquality;
            result.faces.push_back(std::move(face));
        } catch(const std::exception& error) {
            result.issues.push_back(*graph.edges[directed.front()/2].fragment.routes.begin()+": "+error.what());
        }
    }
}
}
