#include "file_io.h"
#include <CommonCrypto/CommonDigest.h>
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <sstream>

namespace ga::bridge {

std::string jsonEscape(const std::string& value) {
    std::ostringstream result;
    for (const unsigned char ch : value) {
        switch (ch) {
        case '\"': result << "\\\""; break;
        case '\\': result << "\\\\"; break;
        case '\b': result << "\\b"; break;
        case '\f': result << "\\f"; break;
        case '\n': result << "\\n"; break;
        case '\r': result << "\\r"; break;
        case '\t': result << "\\t"; break;
        default:
            if (ch < 0x20) {
                result << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                       << static_cast<int>(ch) << std::dec;
            } else {
                result << ch;
            }
        }
    }
    return result.str();
}

std::string sha256File(const std::string& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        return {};
    }

    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    char buffer[64 * 1024];
    while (input.good()) {
        input.read(buffer, sizeof(buffer));
        const auto count = input.gcount();
        if (count > 0) {
            CC_SHA256_Update(&context, buffer, static_cast<CC_LONG>(count));
        }
    }

    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(digest, &context);
    std::ostringstream result;
    result << std::hex << std::setfill('0');
    for (const unsigned char byte : digest) {
        result << std::setw(2) << static_cast<int>(byte);
    }
    return result.str();
}

std::size_t fileSize(const std::string& path) {
    std::ifstream input(path, std::ios::binary | std::ios::ate);
    if (!input) return 0;
    const std::streampos end = input.tellg();
    return end > 0 ? static_cast<std::size_t>(end) : 0;
}

std::string parentDirectory(const std::string& path) {
    const auto separator = path.find_last_of("/\\");
    return separator == std::string::npos ? std::string(".")
                                          : path.substr(0, separator);
}

bool writeAtomicText(const std::string& path, const std::string& payload) {
    const std::string temporary = path + ".tmp";
    std::ofstream output(temporary, std::ios::binary | std::ios::trunc);
    output << payload;
    output.close();
    if (!output || std::rename(temporary.c_str(), path.c_str()) != 0) {
        std::remove(temporary.c_str());
        return false;
    }
    return true;
}

}  // namespace ga::bridge
