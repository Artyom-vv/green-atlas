#include "semantic_evidence.h"
#include <cassert>
#include <limits>
#include <memory>
#include <stdexcept>

using namespace ga::capture;
const std::string identity = "{\"source_route\":\"A/B\",\"archive_route\":\"C/D\",\"archive_file\":\"xref-1.dwg\"}";
SemanticValue captured() {
    SemanticValue value;
    value.origin = "attribute_instance_value";
    value.value = "Труба \\\"400\\\"\n";
    value.tag = "ROLE";
    value.anchorLocal = ga::bridge::Point3{1, 2, 3};
    value.anchorWcs = ga::bridge::Point3{11, 22, 33};
    return value;
}
template<class F> void refuses(F action) {
    bool refused = false;
    try { action(); } catch (const std::runtime_error&) { refused = true; }
    assert(refused);
}
int main() {
    static_assert(sizeof(std::unique_ptr<SemanticValue>) <= 2 * sizeof(void*));
    const std::string hash(64, 'a');
    SemanticCatalogue catalogue;
    const auto measured = captured();
    catalogue.consider(0, identity, false, nullptr);
    catalogue.consider(3, identity, true, &measured);
    const auto qualified = catalogue.receipt(hash, hash, 4);
    assert(qualified.find("\"instance_index\":3") != std::string::npos);
    assert(qualified.find("\"anchor_local\":[1,2,3]") != std::string::npos);
    assert(qualified.find("\"anchor_wcs\":[11,22,33]") != std::string::npos);
    assert(qualified.find("\"eligible_count\":1") != std::string::npos);
    assert(qualified.find("\"complete\":true") != std::string::npos);
    SemanticValue unavailable;
    unavailable.origin = "text_annotation";
    unavailable.status = "value_limit";
    catalogue.consider(4, identity, true, &unavailable);
    catalogue.consider(5, identity, true, nullptr);
    const auto partial = catalogue.receipt(hash, hash, 6);
    assert(partial.find("\"complete\":false") != std::string::npos);
    assert(partial.find("\"emitted_count\":2") != std::string::npos);
    assert(partial.find("\"omitted_count\":1") != std::string::npos);
    auto bad = captured();
    bad.status = "unreadable";
    refuses([&] { catalogue.consider(6, identity, true, &bad); });
    bad = captured();
    bad.value.assign(kMaximumSemanticValueBytes + 1, 'a');
    refuses([&] { catalogue.consider(6, identity, true, &bad); });
    bad = captured();
    bad.anchorLocal->x = std::numeric_limits<double>::quiet_NaN();
    refuses([&] { catalogue.consider(6, identity, true, &bad); });
    refuses([&] { catalogue.consider(6, identity, false, &measured); });
    refuses([&] { catalogue.consider(6, "broken", true, &measured); });

    SemanticCatalogue rowLimit;
    auto shortValue = captured(); shortValue.value.clear(); shortValue.tag.clear();
    for (std::size_t i = 0; i <= kMaximumSemanticRows; ++i)
        rowLimit.consider(i, "{}", true, &shortValue);
    const auto limited = rowLimit.receipt(hash, hash, kMaximumSemanticRows + 1);
    assert(rowLimit.emitted() == kMaximumSemanticRows);
    assert(limited.find("\"omitted_count\":1") != std::string::npos);
    assert(limited.size() <= kMaximumSemanticPayloadBytes);

    SemanticCatalogue byteLimit;
    auto longValue = captured(); longValue.value.assign(kMaximumSemanticValueBytes, '\n');
    for (std::size_t i = 0; i < 5000; ++i) byteLimit.consider(i, identity, true, &longValue);
    const auto byteLimited = byteLimit.receipt(hash, hash, 5000);
    assert(byteLimit.emitted() < 5000);
    assert(byteLimited.size() <= kMaximumSemanticPayloadBytes);
    assert(byteLimited.find("\"complete\":false") != std::string::npos);
}
