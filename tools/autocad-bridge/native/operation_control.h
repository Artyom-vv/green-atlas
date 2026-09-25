#pragma once
#include <cstddef>
#include <functional>
#include <string>

namespace ga::bridge {

// One synchronous capture runs on AutoCAD's main thread. Geometry depends only
// on these callbacks, not on a queue, editor UI or transport implementation.
struct OperationControl {
    std::function<bool()> cancelled;
    std::function<void(const std::string&, std::size_t)> progress;
};

class ScopedOperationControl {
public:
    explicit ScopedOperationControl(OperationControl control);
    ~ScopedOperationControl();
    ScopedOperationControl(const ScopedOperationControl&) = delete;
    ScopedOperationControl& operator=(const ScopedOperationControl&) = delete;
private:
    OperationControl previous_;
};

bool operationCancelled();
void reportOperationProgress(const std::string& phase, std::size_t processedEntities);

}  // namespace ga::bridge
