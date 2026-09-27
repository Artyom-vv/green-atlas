#include "operation_control.h"
#include <utility>

namespace ga::bridge {

namespace {
thread_local OperationControl current;
}

ScopedOperationControl::ScopedOperationControl(OperationControl control)
    : previous_(std::move(current)) {
    current = std::move(control);
}

ScopedOperationControl::~ScopedOperationControl() {
    current = std::move(previous_);
}

bool operationCancelled() {
    return current.cancelled && current.cancelled();
}

void reportOperationProgress(const std::string& phase, std::size_t processedEntities) {
    if (current.progress) current.progress(phase, processedEntities);
}

}  // namespace ga::bridge
