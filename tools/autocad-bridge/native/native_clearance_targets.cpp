#include "native_clearance_targets.h"
#include "native_area_group.h"
#include "native_face_catalog.h"
#include "reviewed_closure.h"
#include "xref_instance_access.h"
#include <set>
#include <stdexcept>

namespace ga::clearance {
namespace {
std::string parent(const std::string& route) {
    const auto index=route.find_last_of('/');return index==std::string::npos?"":route.substr(0,index);
}
}
Mask prepareTarget(AcDbDatabase& database,const ga::nativeQuery::ObjectTarget& target,
                   double distance,const Cancel& cancelled) {
    using namespace ga::nativeQuery;
    if(cancelled&&cancelled()) throw std::runtime_error("clearance cancelled");
    if(target.faceId) {
        if(target.capability!=QueryCapability::Area||!target.additionalRoutes.empty())
            throw std::runtime_error("invalid clearance face target");
        auto region=ga::faces::copyFaceRegion(database,target.faceId,target.route);
        return aroundArea(*region,AcGeMatrix3d::kIdentity,distance,cancelled);
    }
    ga::xref::Instance instance;ga::xref::resolve(database,target.route,instance);
    if(!target.additionalRoutes.empty()) {
        if(target.capability!=QueryCapability::Area||target.additionalRoutes.size()>=kAreaGroupMemberLimit)
            throw std::runtime_error("invalid clearance group target");
        std::set<std::string> unique{target.route};
        std::vector<std::unique_ptr<ga::xref::Instance>> members;
        std::vector<AcDbEntity*> curves{instance.entity};
        for(const auto& route:target.additionalRoutes) {
            if(!unique.insert(route).second||parent(route)!=parent(target.route))
                throw std::runtime_error("invalid clearance group instance");
            auto member=std::make_unique<ga::xref::Instance>();ga::xref::resolve(database,route,*member);
            if(member->entity->ownerId()!=instance.entity->ownerId()||member->entity->layerId()!=instance.entity->layerId())
                throw std::runtime_error("clearance group requires one definition/layer");
            curves.push_back(member->entity);members.push_back(std::move(member));
        }
        AreaGroupQuery group;group.prepare(curves,instance.transform);
        auto region=group.copyWorldRegion();
        return aroundArea(*region,AcGeMatrix3d::kIdentity,distance,cancelled);
    }
    if(target.capability==QueryCapability::Curve)
        return aroundCurve(*instance.entity,instance.transform,distance,cancelled);
    ReviewedClosure closure;
    auto* input=target.capability==QueryCapability::ClosedArea
        ?static_cast<AcDbEntity*>(&closure.prepare(*instance.entity)):instance.entity;
    ga::direct::Prepared owner;auto* region=ga::direct::prepareLocalArea(input,owner);
    return aroundArea(*region,instance.transform,distance,cancelled);
}
}
