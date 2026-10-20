#pragma once
#include <algorithm>
#include <array>
#include <cstring>
#include <streambuf>

namespace ga::bridge {

// A formatting adapter for an already serialized JSON stream, not a parser.
// Only the four JSON whitespace bytes outside quoted strings are removed.
// Number lexemes, escapes, field order and UTF-8 bytes pass through unchanged.
class CompactJsonBuffer final : public std::streambuf {
public:
    static constexpr std::size_t kBufferBytes = 64 * 1024;

    explicit CompactJsonBuffer(std::streambuf& destination)
        : destination_(destination) {
        reset();
    }

protected:
    int_type overflow(int_type character) override {
        if (!flushPending()) return traits_type::eof();
        if (!traits_type::eq_int_type(character, traits_type::eof())) {
            *pptr() = traits_type::to_char_type(character);
            pbump(1);
        }
        return traits_type::not_eof(character);
    }

    std::streamsize xsputn(const char* data, std::streamsize count) override {
        std::streamsize written = 0;
        while (written < count && !failed_) {
            if (pptr() == epptr() && !flushPending()) break;
            const auto chunk = std::min<std::streamsize>(
                count - written, epptr() - pptr());
            std::memcpy(pptr(), data + written, static_cast<std::size_t>(chunk));
            pbump(static_cast<int>(chunk));
            written += chunk;
        }
        return written;
    }

    int sync() override {
        if (!flushPending() || destination_.pubsync() != 0) {
            failed_ = true;
            return -1;
        }
        return 0;
    }

private:
    static bool whitespace(char byte) {
        return byte == ' ' || byte == '\t' || byte == '\n' || byte == '\r';
    }

    void reset() { setp(bytes_.data(), bytes_.data() + bytes_.size()); }

    bool flushPending() {
        if (failed_) return false;
        char* compact = pbase();
        for (char* current = pbase(); current != pptr(); ++current) {
            const char byte = *current;
            if (!quoted_ && whitespace(byte)) continue;
            *compact++ = byte;
            if (quoted_) {
                if (escaped_) escaped_ = false;
                else if (byte == '\\') escaped_ = true;
                else if (byte == '"') quoted_ = false;
            } else if (byte == '"') {
                quoted_ = true;
            }
        }
        const auto count = compact - pbase();
        if (count && destination_.sputn(pbase(), count) != count) {
            failed_ = true;
            return false;
        }
        reset();
        return true;
    }

    std::streambuf& destination_;
    std::array<char, kBufferBytes> bytes_{};
    bool quoted_ = false;
    bool escaped_ = false;
    bool failed_ = false;
};

}  // namespace ga::bridge
