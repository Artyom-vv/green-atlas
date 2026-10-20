#include "semantic_evidence.h"
#include "file_io.h"
#include <cmath>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace ga::capture {
namespace {
std::string quoted(const std::string& text) {
    return "\"" + ga::bridge::jsonEscape(text) + "\"";
}
void anchor(std::ostream& out, const std::optional<ga::bridge::Point3>& point) {
    if (!point) { out << "null"; return; }
    if (!std::isfinite(point->x) || !std::isfinite(point->y) || !std::isfinite(point->z))
        throw std::runtime_error("semantic anchor is not finite");
    out << '[' << point->x << ',' << point->y << ',' << point->z << ']';
}
std::string row(std::size_t index, const std::string& identity,
                const SemanticValue& value) {
    if (identity.size() < 2 || identity.front() != '{' || identity.back() != '}')
        throw std::runtime_error("semantic instance identity unavailable");
    if (value.value.size() > kMaximumSemanticValueBytes ||
        value.tag.size() > kMaximumSemanticValueBytes)
        throw std::runtime_error("semantic value exceeds capture contract");
    const bool captured = value.status == "captured";
    if (captured && (!value.anchorLocal || !value.anchorWcs))
        throw std::runtime_error("captured semantic anchor unavailable");
    if (!captured && (!value.value.empty() || !value.tag.empty() ||
                     value.anchorLocal || value.anchorWcs))
        throw std::runtime_error("incomplete semantic row carries unqualified values");
    std::ostringstream out;
    out << std::setprecision(17) << "{\"instance_index\":" << index << ','
        << identity.substr(1, identity.size() - 2)
        << ",\"origin\":" << quoted(value.origin)
        << ",\"status\":" << quoted(value.status)
        << ",\"value\":" << quoted(value.value) << ",\"tag\":" << quoted(value.tag)
        << ",\"invisible\":" << (value.invisible ? "true" : "false")
        << ",\"anchor_local\":";
    anchor(out, value.anchorLocal);
    out << ",\"anchor_wcs\":";
    anchor(out, value.anchorWcs);
    out << '}';
    return out.str();
}
}

void SemanticCatalogue::consider(std::size_t index, const std::string& identity,
                                 bool eligible, const SemanticValue* value) {
    if (!eligible) {
        if (value) throw std::runtime_error("semantic value on ineligible instance");
        return;
    }
    ++eligibleCount_;
    if (!value || emittedCount_ >= kMaximumSemanticRows) { ++omittedCount_; return; }
    const auto encoded = row(index, identity, *value);
    // Reserve enough for the fixed-size receipt and counters. No row or text is
    // cut in the middle; the omission is explicit in the source-bound receipt.
    constexpr std::size_t kReceiptReserveBytes = 1024;
    if (rows_.size() + encoded.size() + 1 >
        kMaximumSemanticPayloadBytes - kReceiptReserveBytes) {
        ++omittedCount_;
        return;
    }
    if (emittedCount_) rows_ += ',';
    rows_ += encoded;
    ++emittedCount_;
    allCaptured_ = allCaptured_ && value->status == "captured";
}

std::string SemanticCatalogue::receipt(const std::string& instancesSha256,
                                       const std::string& displaySha256,
                                       std::size_t instanceCount) const {
    std::ostringstream out;
    out << "{\"schema\":\"green-atlas.native-semantics/1\",\"instances_sha256\":"
        << quoted(instancesSha256) << ",\"display_sha256\":" << quoted(displaySha256)
        << ",\"instance_count\":" << instanceCount
        << ",\"eligible_count\":" << eligibleCount_ << ",\"emitted_count\":" << emittedCount_
        << ",\"omitted_count\":" << omittedCount_ << ",\"complete\":"
        << (allCaptured_ && omittedCount_ == 0 ? "true" : "false")
        << ",\"rows\":[" << rows_ << "]}";
    return out.str();
}
}
