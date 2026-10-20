#include "semantic_capture.h"
#include "AcString.h"
#include "dbents.h"
#include "dbmtext.h"
#include "gemat3d.h"
#include <cmath>
#include <memory>
#include <stdexcept>

namespace ga::capture {
namespace {
void checked(Acad::ErrorStatus status) {
    if (status != Acad::eOk) throw std::runtime_error("native semantic value unavailable");
}
std::string origin(const AcDbEntity& entity) {
    if (entity.isA() == AcDbAttribute::desc()) return "attribute_instance_value";
    if (entity.isA() == AcDbAttributeDefinition::desc()) return "attribute_definition_default";
    if (entity.isA() == AcDbMText::desc()) return "mtext_annotation";
    if (entity.isA() == AcDbText::desc()) return "text_annotation";
    return {};
}
bool finite(const AcGePoint3d& point) {
    return std::isfinite(point.x) && std::isfinite(point.y) && std::isfinite(point.z);
}
void discard(SemanticValue& value, const char* status) {
    value.status = status;
    value.value.clear(); value.tag.clear();
    value.anchorLocal.reset(); value.anchorWcs.reset();
}
}
bool semanticEligible(const AcDbEntity& entity) { return !origin(entity).empty(); }

std::unique_ptr<SemanticValue> readSemanticValue(AcDbEntity& entity,
                                              const AcGeMatrix3d& transform,
                                              SemanticReadBudget& budget) {
    const auto kind = origin(entity);
    if (kind.empty()) return {};
    if (++budget.eligibleCount > kMaximumSemanticRows) return {};
    auto retained = std::make_unique<SemanticValue>();
    auto& result = *retained;
    result.origin = kind;
    if (budget.retainedValueBytes >= kMaximumSemanticValueBudget) {
        discard(result, "budget_limit"); return retained;
    }
    try {
        AcString value, tag;
        AcGePoint3d point;
        if (auto* attribute = AcDbAttribute::cast(&entity)) {
            checked(attribute->tag(tag));
            result.invisible = attribute->isInvisible();
            point = attribute->position();
            if (attribute->isMTextAttribute()) {
                // Autodesk transfers ownership of this detached clone.
                std::unique_ptr<AcDbMText> text(attribute->getMTextAttribute());
                if (!text) throw std::runtime_error("multiline attribute unavailable");
                checked(text->contents(value));
            } else checked(attribute->textString(value));
        } else if (auto* definition = AcDbAttributeDefinition::cast(&entity)) {
            checked(definition->tag(tag));
            result.invisible = definition->isInvisible();
            point = definition->position();
            if (definition->isMTextAttributeDefinition()) {
                std::unique_ptr<AcDbMText> text(definition->getMTextAttributeDefinition());
                if (!text) throw std::runtime_error("multiline definition unavailable");
                checked(text->contents(value));
            } else checked(definition->textString(value));
        } else if (auto* text = AcDbMText::cast(&entity)) {
            checked(text->contents(value)); point = text->location();
        } else if (auto* text = AcDbText::cast(&entity)) {
            checked(text->textString(value)); point = text->position();
        }
        // Wide character count rejects obvious overflow before a UTF-8 copy;
        // final byte limits account for multibyte CAD content independently.
        if (value.length() > kMaximumSemanticValueBytes || tag.length() > kMaximumSemanticValueBytes) {
            discard(result, "value_limit"); return retained;
        }
        result.value = value.utf8Str(); result.tag = tag.utf8Str();
        if (result.value.size() > kMaximumSemanticValueBytes ||
            result.tag.size() > kMaximumSemanticValueBytes) {
            discard(result, "value_limit"); return retained;
        }
        const auto bytes = result.value.size() + result.tag.size();
        if (bytes > kMaximumSemanticValueBudget - budget.retainedValueBytes) {
            discard(result, "budget_limit"); return retained;
        }
        const auto local = point;
        const auto world = point.transformBy(transform);
        if (!finite(local) || !finite(world)) {
            discard(result, "invalid_anchor"); return retained;
        }
        result.anchorLocal = ga::bridge::Point3{local.x, local.y, local.z};
        result.anchorWcs = ga::bridge::Point3{world.x, world.y, world.z};
        budget.retainedValueBytes += bytes;
    } catch (const std::exception&) { discard(result, "unreadable"); }
    return retained;
}
}
