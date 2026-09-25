#include "native_clearance_domain.h"
#include "native_clearance_targets.h"
#include <chrono>
#include <cmath>
#include <stdexcept>

namespace ga::clearance {
namespace {
Region copy(const Region& region) {return region?cloneRegion(*region):nullptr;}
double size(const Region& region) {return region?area(*region):0;}
void remove(Region& from,const AcDbRegion* obstacle) {
    if(from&&obstacle) from=subtract(*from,*obstacle);
}
void intersect(Region& from,const AcDbRegion* with) {
    if(!from) return;
    if(!with) {from.reset();return;}
    auto other=cloneRegion(*with);
    const auto status=from->booleanOper(AcDb::kBoolIntersect,other.get());
    if(status!=Acad::eOk) throw std::runtime_error("domain intersection status "+std::to_string(int(status)));
    if(from->isNull()||area(*from)==0) from.reset();
}
Region reviewEnvelope(const Constraint& item) {
    if(!item.bounds) return nullptr; // report a global, unlocatable source gap
    const auto b=*item.bounds;const auto d=item.reviewDistance;
    if(!std::isfinite(d)||d<0||b[0]>b[2]||b[1]>b[3]) throw std::runtime_error("invalid domain review bounds");
    // This is explicitly a review envelope, NEVER an occupied CAD area.
    return polygon({{b[0]-d,b[1]-d,0},{b[2]+d,b[1]-d,0},{b[2]+d,b[3]+d,0},{b[0]-d,b[3]+d,0}});
}
}
Domain::Domain(const AcDbRegion& work):possible(cloneRegion(work)),available(cloneRegion(work)),workArea(area(work)) {}
Domain::Domain(const Domain& other):possible(copy(other.possible)),available(copy(other.available)),sites(copy(other.sites)),
    siteSeen(other.siteSeen),progress(other.progress),issues(other.issues),complete(other.complete),workArea(other.workArea) {}
void Domain::advance(AcDbDatabase& db,Cache& cache,const std::vector<Constraint>& batch,bool final,const Cancel& cancelled) {
    if(complete) throw std::runtime_error("domain already complete");
    const auto start=std::chrono::steady_clock::now();
    for(const auto& item:batch) {
        if(cancelled&&cancelled()) throw std::runtime_error("clearance cancelled");
        if(!std::isfinite(item.distance)||item.distance<0) throw std::runtime_error("invalid constraint distance");
        const bool site=item.participation==Participation::Site;
        siteSeen=siteSeen||site;
        std::string failure=item.sourceError;
        const AcDbRegion* mask=nullptr;
        if(failure.empty()) {
            const auto& result=cache.get(item.target,item.distance,[&]{return prepareTarget(db,item.target,item.distance,cancelled);},cancelled);
            progress.cacheHits+=result.hit;failure=result.error;mask=result.mask.region.get();
        }
        if(!failure.empty()) {
            auto review=reviewEnvelope(item);
            issues.push_back({item.target.route,failure,item.target.faceId,bool(review)});
            remove(available,review.get());
        } else if(site) {
            if(!mask) throw std::runtime_error("site mask has no area");
            if(!sites) sites=cloneRegion(*mask);
            else {
                auto other=cloneRegion(*mask);
                if(sites->booleanOper(AcDb::kBoolUnite,other.get())!=Acad::eOk)
                    throw std::runtime_error("domain site union failed");
            }
        } else {
            remove(available,mask);
            if(item.participation==Participation::Obstacle) remove(possible,mask);
        }
        ++progress.processed;
    }
    if(final) {
        if(siteSeen&&sites) {intersect(possible,sites.get());intersect(available,sites.get());}
        else {
            // A manual work polygon is not proof of membership in source site.
            available.reset();issues.push_back({"","site_membership",0,false});
        }
        complete=true;
    }
    progress.elapsedSeconds+=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
}
double Domain::availableArea() const {return complete?size(available):0;}
double Domain::excludedArea() const {return std::max(0.,workArea-size(possible));}
double Domain::unresolvedArea() const {return complete?std::max(0.,size(possible)-size(available)):0;}
Region Domain::unresolvedRegion() const {
    if(!complete||!possible) return nullptr;
    return available?subtract(*possible,*available):cloneRegion(*possible);
}
}
