#include "compact_json.h"
#include <cassert>
#include <iostream>
#include <sstream>
#include <string>

using ga::bridge::CompactJsonBuffer;

std::string compact(const std::string& serialized, bool flushEachByte = false) {
    std::stringbuf destination;
    CompactJsonBuffer buffer(destination);
    std::ostream output(&buffer);
    if (flushEachByte) {
        for (char byte : serialized) { output.put(byte); output.flush(); }
    } else {
        output << serialized;
    }
    output.flush();
    assert(output);
    return destination.str();
}

class FailingDestination : public std::streambuf {
public:
    explicit FailingDestination(bool shortWrite) : shortWrite_(shortWrite) {}
protected:
    std::streamsize xsputn(const char*, std::streamsize count) override {
        return shortWrite_ ? count - 1 : count;
    }
    int sync() override { return -1; }
private:
    bool shortWrite_;
};

void checkFormatting() {
    const std::string source =
        " \r\n\t{ \"слой с пробелом\" : \"  снег \\\"  \\\\ путь\\t\\n\\u0410 \" , "
        "\"numbers\" : [ -0.0, 0.12345678901234566, 1.2345678901234567e+018, 9007199254740993 ], "
        "\"truth\" : true, \"empty\" : null } \n";
    const std::string expected =
        "{\"слой с пробелом\":\"  снег \\\"  \\\\ путь\\t\\n\\u0410 \","
        "\"numbers\":[-0.0,0.12345678901234566,1.2345678901234567e+018,9007199254740993],"
        "\"truth\":true,\"empty\":null}";
    assert(compact(source) == expected);
    assert(compact(source, true) == expected); // Quote/escape state survives each flush.
    assert(compact(expected) == expected);
    // Do not broaden whitespace removal to bytes that JSON does not permit.
    assert(compact(" \v\f \"\t \r\n\" ") == "\v\f\"\t \r\n\"");

    std::string quoted(CompactJsonBuffer::kBufferBytes - 2, ' ');
    quoted += "\\\"\\\\ э\"";
    std::string acrossBuffers = " { \"key\" : \"" + quoted + " } \n";
    assert(compact(acrossBuffers) == "{\"key\":\"" + quoted + "}");
    std::string utf8(CompactJsonBuffer::kBufferBytes - 2, 'a');
    utf8 += "э"; // A multibyte character crosses the formatter buffer boundary.
    assert(compact("\"" + utf8 + "\"") == "\"" + utf8 + "\"");
}

void checkFailures() {
    for (bool shortWrite : {false, true}) {
        FailingDestination destination(shortWrite);
        CompactJsonBuffer buffer(destination);
        std::ostream output(&buffer);
        output << " { \"value\" : 1 } ";
        output.flush();
        assert(output.bad()); // Both failed write and failed destination sync propagate.
    }
}

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--compact") {
        CompactJsonBuffer buffer(*std::cout.rdbuf());
        std::ostream output(&buffer);
        std::array<char, CompactJsonBuffer::kBufferBytes> chunk;
        while (std::cin.read(chunk.data(), chunk.size()) || std::cin.gcount()) {
            output.write(chunk.data(), std::cin.gcount());
        }
        output.flush();
        return output && !std::cin.bad() ? 0 : 1;
    }
    assert(argc == 1);
    checkFormatting();
    checkFailures();
}
