#pragma once
#include <string>

namespace ga::bridge {
// Source identity keeps the authored layer. Only calculation/display inherits
// layer 0 and applies the namespace of a separately opened XREF database.
inline std::string resolveInstanceLayer(const std::string& authored,
                                       const std::string& inherited,
                                       const std::string& nameSpace) {
    const std::string layer = authored == "0" ? inherited : authored;
    if (nameSpace.empty() || layer.empty() || layer == "0" ||
        layer.rfind(nameSpace + "|", 0) == 0) return layer;
    return nameSpace + "|" + layer;
}
}
