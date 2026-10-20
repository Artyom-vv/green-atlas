#include "file_io.h"
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <bcrypt.h>
#include <filesystem>
#include <vector>
#else
#include <CommonCrypto/CommonDigest.h>
#endif
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <sstream>

namespace ga::bridge {

#ifdef _WIN32
namespace {
// Строки протокола содержат UTF-8; файловые API Windows получают UTF-16.
std::filesystem::path nativePath(const std::string& value) {
    return std::filesystem::u8path(value);
}
}
#endif

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
#ifdef _WIN32
    std::ifstream input(nativePath(path), std::ios::binary);
#else
    std::ifstream input(path, std::ios::binary);
#endif
    if (!input) {
        return {};
    }

#ifdef _WIN32
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    std::vector<unsigned char> object;
    struct Cleanup {
        BCRYPT_ALG_HANDLE& algorithm;
        BCRYPT_HASH_HANDLE& hash;
        ~Cleanup() {
            if (hash) BCryptDestroyHash(hash);
            if (algorithm) BCryptCloseAlgorithmProvider(algorithm, 0);
        }
    } cleanup{algorithm, hash};
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0)
        return {};
    ULONG objectSize = 0, written = 0;
    if (BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH,
            reinterpret_cast<PUCHAR>(&objectSize), sizeof(objectSize), &written, 0) < 0
        || written != sizeof(objectSize) || objectSize == 0)
        return {};
    object.resize(objectSize);
    if (BCryptCreateHash(algorithm, &hash, object.data(), objectSize, nullptr, 0, 0) < 0)
        return {};
#else
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
#endif
    char buffer[64 * 1024];
    while (input.good()) {
        input.read(buffer, sizeof(buffer));
        const auto count = input.gcount();
        if (count > 0) {
#ifdef _WIN32
            if (BCryptHashData(hash, reinterpret_cast<PUCHAR>(buffer),
                              static_cast<ULONG>(count), 0) < 0) return {};
#else
            CC_SHA256_Update(&context, buffer, static_cast<CC_LONG>(count));
#endif
        }
    }

#ifdef _WIN32
    if (!input.eof()) return {};
    unsigned char digest[32];
    if (BCryptFinishHash(hash, digest, sizeof(digest), 0) < 0) return {};
#else
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(digest, &context);
#endif
    std::ostringstream result;
    result << std::hex << std::setfill('0');
    for (const unsigned char byte : digest) {
        result << std::setw(2) << static_cast<int>(byte);
    }
    return result.str();
}

std::size_t fileSize(const std::string& path) {
#ifdef _WIN32
    std::ifstream input(nativePath(path), std::ios::binary | std::ios::ate);
#else
    std::ifstream input(path, std::ios::binary | std::ios::ate);
#endif
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
#ifdef _WIN32
    std::ofstream output(nativePath(temporary), std::ios::binary | std::ios::trunc);
#else
    std::ofstream output(temporary, std::ios::binary | std::ios::trunc);
#endif
    output << payload;
    output.close();
#ifdef _WIN32
    if (!output || !MoveFileExW(nativePath(temporary).c_str(), nativePath(path).c_str(),
                               MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        std::error_code error;
        std::filesystem::remove(nativePath(temporary), error);
#else
    if (!output || std::rename(temporary.c_str(), path.c_str()) != 0) {
        std::remove(temporary.c_str());
#endif
        return false;
    }
    return true;
}

}  // namespace ga::bridge
