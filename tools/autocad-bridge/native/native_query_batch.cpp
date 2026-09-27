#include "native_query_batch.h"
#include "native_affine_query.h"
#include "native_area_group.h"
#include "native_curve_query.h"
#include "native_face_catalog.h"
#include "reviewed_closure.h"
#include "xref_instance_access.h"
#include "AcString.h"
#include <chrono>
#include <cmath>
#include <set>
#include <stdexcept>

namespace ga::nativeQuery {
namespace {
using Clock = std::chrono::steady_clock;
void checkCancellation(const std::function<bool()>& cancelled) {
    if(cancelled && cancelled()) throw std::runtime_error("native query cancelled");
}
std::string parentRoute(const std::string& route) {
    const auto last=route.find_last_of('/');
    return last==std::string::npos?"":route.substr(0,last);
}
ObjectMeasurements measureObject(AcDbDatabase& database, const ObjectTarget& target,
    const std::vector<AcGePoint3d>& points, const std::function<bool()>& cancelled) {
    ObjectMeasurements result;
    result.route = target.route;
    result.additionalRoutes = target.additionalRoutes;
    result.faceId = target.faceId;
    const auto start = Clock::now();
    // Declaration order is intentional: BReps must die before their DB entities.
    ga::xref::Instance instance;
    std::vector<std::unique_ptr<ga::xref::Instance>> members;
    ReviewedClosure reviewed;
    std::unique_ptr<AreaGroupQuery> group;
    std::unique_ptr<AffineAreaQuery> area;
    std::unique_ptr<CurveQuery> curve;
    try {
        ga::xref::resolve(database, target.route, instance);
        result.entityType = AcString(instance.entity->isA()->name()).utf8Str();
        AcString layer; instance.entity->layer(layer); result.layer = layer.utf8Str();
        if(target.faceId) {
            // Catalogue identity is checked even for a batch without an inside point.
            ga::faces::queryFace(database,target.faceId,target.route,points.front());
            result.capability="area"; result.interiorKnown=true;
        } else if(!target.additionalRoutes.empty()) {
            std::vector<AcDbEntity*> curves{instance.entity};
            for(const auto& route:target.additionalRoutes) {
                if(parentRoute(route)!=parentRoute(target.route))
                    throw std::runtime_error("group members belong to different instances");
                auto member=std::make_unique<ga::xref::Instance>();
                ga::xref::resolve(database,route,*member);
                if(member->entity->ownerId()!=instance.entity->ownerId()
                    ||member->entity->layerId()!=instance.entity->layerId())
                    throw std::runtime_error("group members require one definition/layer");
                curves.push_back(member->entity); members.push_back(std::move(member));
            }
            group=std::make_unique<AreaGroupQuery>(); group->prepare(curves,instance.transform);
            result.capability="area"; result.interiorKnown=true;
        } else if(target.capability == QueryCapability::Area || target.capability == QueryCapability::ClosedArea) {
            try {
                area = std::make_unique<AffineAreaQuery>();
                auto& input = target.capability == QueryCapability::ClosedArea
                    ? static_cast<AcDbEntity&>(reviewed.prepare(*instance.entity)) : *instance.entity;
                area->prepare(input, instance.transform);
                result.capability = "area";
                result.interiorKnown = true;
            } catch(const std::exception& error) {
                area.reset();
                result.preparationError = error.what();
            }
        }
        if(!area&&!group&&!target.faceId) {
            // Curve distance remains useful, but never stands in for an interior.
            curve = std::make_unique<CurveQuery>();
            curve->prepare(*instance.entity, instance.transform);
            result.capability = "curve";
        }
    } catch(const std::exception& error) {
        area.reset(); curve.reset(); group.reset();
        result.capability = "unavailable";
        result.interiorKnown = false;
        if(!result.preparationError.empty()) result.preparationError += "; ";
        result.preparationError += error.what();
    }
    result.prepareMs = std::chrono::duration<double, std::milli>(Clock::now()-start).count();
    result.answers.reserve(points.size()); result.queryErrors.reserve(points.size());
    for(const auto& point: points) {
        checkCancellation(cancelled);
        ga::direct::Answer answer;
        std::string error;
        try {
            if(target.faceId&&result.interiorKnown) answer=ga::faces::queryFace(database,target.faceId,target.route,point);
            else if(group) answer = group->queryPlanar(point);
            else if(area) answer = area->queryPlanar(point);
            else if(curve) answer = curve->queryPlanar(point);
            else error = result.preparationError;
        } catch(const std::exception& failure) { error = failure.what(); }
        result.answers.push_back(answer);
        result.queryErrors.push_back(error);
    }
    return result;
}
}
BatchMeasurements measureBatch(AcDbDatabase& database,
    const std::vector<ObjectTarget>& targets, const std::vector<AcGePoint3d>& points,
    const std::function<bool()>& cancelled) {
    if(targets.empty() || targets.size()>kBatchObjectLimit || points.empty()
        || points.size()>kBatchPointLimit || targets.size()*points.size()>kBatchMeasurementLimit)
        throw std::runtime_error("native query batch limits exceeded or empty batch");
    std::set<std::pair<std::string,unsigned>> routes;
    std::size_t totalMembers=0;
    for(const auto& target:targets) {
        if(!routes.insert({target.route,target.faceId}).second) throw std::runtime_error("duplicate native query target");
        if(target.faceId&&(target.capability!=QueryCapability::Area||!target.additionalRoutes.empty()))
            throw std::runtime_error("invalid derived face target");
        if(!target.additionalRoutes.empty()&&(target.capability!=QueryCapability::Area
            ||target.additionalRoutes.size()>=kAreaGroupMemberLimit))
            throw std::runtime_error("invalid native group capability/size");
        std::set<std::string> members{target.route};
        for(const auto& route:target.additionalRoutes)
            if(!members.insert(route).second) throw std::runtime_error("duplicate native group member");
        totalMembers+=members.size();
    }
    if(totalMembers>kBatchObjectLimit) throw std::runtime_error("native group total member limit");
    for(const auto& p:points)
        if(!std::isfinite(p.x)||!std::isfinite(p.y)||!std::isfinite(p.z))
            throw std::runtime_error("nonfinite native batch point");
    const auto start = Clock::now();
    BatchMeasurements result;
    result.objects.reserve(targets.size());
    for(const auto& target:targets) {
        checkCancellation(cancelled);
        result.objects.push_back(measureObject(database,target,points,cancelled));
    }
    result.elapsedMs = std::chrono::duration<double, std::milli>(Clock::now()-start).count();
    return result;
}
}
