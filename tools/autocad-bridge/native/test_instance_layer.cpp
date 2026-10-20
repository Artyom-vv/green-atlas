#include "instance_layer.h"
#include <cassert>

int main() {
    using ga::bridge::resolveInstanceLayer;
    assert(resolveInstanceLayer("0", "BUILDING", "") == "BUILDING");
    assert(resolveInstanceLayer("PIPE", "BUILDING", "") == "PIPE");
    assert(resolveInstanceLayer("0", "0", "") == "0");
    assert(resolveInstanceLayer("PIPE", "0", "A|B") == "A|B|PIPE");
    assert(resolveInstanceLayer("A|B|PIPE", "BUILDING", "A|B") == "A|B|PIPE");
    assert(resolveInstanceLayer("0", "A|B|BUILDING", "A|B") == "A|B|BUILDING");
}
