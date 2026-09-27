#import <Foundation/Foundation.h>
#include "reference_search.h"
#include <cstdio>

int main(int argc, const char* argv[]) {
    @autoreleasepool {
        if (argc == 4) {
            NSData* data = [NSData dataWithContentsOfFile:[NSString stringWithUTF8String:argv[2]]];
            NSDictionary* probe = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
            std::vector<gaDelivery::ReferencePathRequest> requests;
            for (NSDictionary* ref in probe[@"xref_dependencies"])
                if ([ref[@"status"] isEqual:@"unresolved"])
                    requests.push_back({[ref[@"block_name"] UTF8String], [ref[@"stored_path"] UTF8String]});
            NSMutableArray* results = [NSMutableArray new];
            for (const auto& match : gaDelivery::findReferencePaths(argv[1], requests))
                [results addObject:@{@"block":[NSString stringWithUTF8String:match.name.c_str()],
                    @"path":[NSString stringWithUTF8String:match.path.c_str()],
                    @"status":[NSString stringWithUTF8String:match.status.c_str()]}];
            return [[NSJSONSerialization dataWithJSONObject:results options:NSJSONWritingPrettyPrinted error:nil]
                writeToFile:[NSString stringWithUTF8String:argv[3]] atomically:YES] ? 0 : 1;
        }
        if (argc != 2) return 2;
        NSString* root = [NSString stringWithUTF8String:argv[1]];
        NSFileManager* fs = NSFileManager.defaultManager;
        auto write = [&](NSString* path, NSString* data) {
            NSString* file = [root stringByAppendingPathComponent:path];
            [fs createDirectoryAtPath:file.stringByDeletingLastPathComponent withIntermediateDirectories:YES attributes:nil error:nil];
            [data writeToFile:file atomically:YES encoding:NSUTF8StringEncoding error:nil];
        };
        write(@"one/сети/вода.dwg", @"one");
        write(@"two/other/вода.dwg", @"two");
        write(@"one/duplicate.dwg", @"identical");
        write(@"two/duplicate.dwg", @"identical");
        write(@"one/road.dwg", @"road");
        [fs createSymbolicLinkAtPath:[root stringByAppendingPathComponent:@"link.dwg"]
            withDestinationPath:[root stringByAppendingPathComponent:@"one/road.dwg"] error:nil];
        std::vector<gaDelivery::ReferencePathRequest> requests = {
            {"suffix", ".\\сети\\вода.dwg"},
            {"ambiguous", "C:\\missing\\вода.dwg"},
            {"identical", "duplicate.dwg"},
            {"unique", "road.dwg"},
            {"missing", "no-file.dwg"},
            {"symlink", "link.dwg"},
            {"outside", "../../road.dwg"},
        };
        const auto found = gaDelivery::findReferencePaths(argv[1], requests);
        const char* expected[] = {"exact_suffix", "ambiguous", "identical_copies",
                                 "unique_name", "missing", "missing", "unique_name"};
        for (size_t i = 0; i < found.size(); ++i) {
            if (found[i].status != expected[i]) {
                std::fprintf(stderr, "%s: expected %s got %s (%s; candidates=%zu)\n", found[i].name.c_str(), expected[i], found[i].status.c_str(), found[i].path.c_str(), found[i].candidates.size());
                return 1;
            }
            if ((found[i].status == "ambiguous" || found[i].status == "missing") && !found[i].path.empty()) return 2;
            if (!found[i].path.empty() && found[i].path.find(std::string(argv[1]) + "/") != 0) return 3;
        }
        std::puts("7 native reference-search cases passed");
        return 0;
    }
}
