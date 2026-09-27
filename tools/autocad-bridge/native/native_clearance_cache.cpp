#include "native_clearance_cache.h"
#include <cmath>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace ga::clearance {
namespace {
void check(const Cancel& cancelled) {
    if(cancelled&&cancelled()) throw std::runtime_error("clearance cancelled");
}
std::string key(const ga::nativeQuery::ObjectTarget& target,double distance) {
    if(!std::isfinite(distance)||distance<0) throw std::runtime_error("invalid clearance cache distance");
    std::ostringstream out;
    out<<std::quoted(target.route)<<':'<<int(target.capability)<<':'<<target.faceId<<':'
       <<std::hexfloat<<(distance==0?0:distance);
    for(const auto& route:target.additionalRoutes) out<<':'<<std::quoted(route);
    return out.str();
}
}
void Cache::clear() {entries.clear();recent.clear();pieces=0;}
const Entry& Cache::get(const ga::nativeQuery::ObjectTarget& target,double distance,
                        const std::function<Mask()>& factory,const Cancel& cancelled) {
    check(cancelled);
    const auto identity=key(target,distance);
    if(auto found=entries.find(identity);found!=entries.end()) {
        recent.splice(recent.end(),recent,found->second.recency);
        found->second.value.hit=true;return found->second.value;
    }
    Entry entry;
    try {entry.mask=factory();}
    catch(const std::exception& e) {check(cancelled);entry.error=e.what();}
    check(cancelled);
    if(entry.mask.boundaryPieces>kCachedBoundaryPieceLimit)
        throw std::runtime_error("single clearance mask exceeds cache budget");
    while(!recent.empty()&&(entries.size()>=kCachedMaskLimit
          ||pieces+entry.mask.boundaryPieces>kCachedBoundaryPieceLimit)) {
        const auto old=entries.find(recent.front());
        pieces-=old->second.value.mask.boundaryPieces;entries.erase(old);recent.pop_front();
    }
    pieces+=entry.mask.boundaryPieces;recent.push_back(identity);
    auto result=entries.emplace(identity,Stored{std::move(entry),std::prev(recent.end())});
    return result.first->second.value;
}
}
