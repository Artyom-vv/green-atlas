#import "project-route.h"
#include <cassert>

int main() {
    @autoreleasepool {
        assert([GAHandoffIdentifier(@"/tmp/transfer.gatransfer") isEqual:@"a9c581f621aaf2f5cbfd2251928da6d60854fba0330aaa311fb4f137d87c2576"]);
        assert(!GAHandoffIdentifier(@"transfer.gatransfer"));
        assert(!GAHandoffIdentifier(@"/tmp/../transfer.gatransfer"));
        assert([GAHandoffIdentifier(@"/tmp/Кустанайская/transfer.gatransfer") isEqual:@"ab6ce794c43360972650ed188e80444e568e065b75ed1e7940fb953c4091dc61"]);
        NSString *prefix = @"/projects/122a1ecd-4e1f-4a4e-bf88-13b93299ae1f/";
        NSString *setup = [prefix stringByAppendingString:@"setup"];
        NSString *opening = [setup stringByAppendingString:@"?open=editor"];
        NSString *legacy = [prefix stringByAppendingString:@"import?source=cad"];
        assert([GAHandoffProjectPath(@{@"entry": @"Drawing.autocad.json", @"project_path": setup}) isEqual:setup]);
        assert([GAHandoffProjectPath(@{@"entry": @"Drawing.autocad.json", @"project_path": opening}) isEqual:opening]);
        assert([GAHandoffProjectPath(@{@"entry": @"Drawing.dxf", @"project_path": legacy}) isEqual:legacy]);
        assert(!GAHandoffProjectPath(@{@"entry": @"Drawing.autocad.json", @"project_path": legacy}));
        assert(!GAHandoffProjectPath(@{@"entry": @"Drawing.dxf", @"project_path": setup}));
        assert(!GAHandoffProjectPath(@{}));
        assert(!GAHandoffProjectPath(@{@"entry": @"Drawing.autocad.json", @"project_path": @42}));
        for (NSString *bad in @[
            [@"https://example.com" stringByAppendingString:setup],
            [setup stringByAppendingString:@"?redirect=https://example.com"],
            [opening stringByAppendingString:@"&redirect=https://example.com"],
            [opening stringByAppendingString:@"#fragment"],
            [setup stringByAppendingString:@"#fragment"],
            [setup stringByAppendingString:@"\n"],
            @"/projects/------------------------------------/setup",
            @"/projects/122a1ecd-4e1f-4a4e-bf88-13b93299ae1f/../setup"
        ]) assert(!GAHandoffProjectPath(@{@"entry": @"Drawing.autocad.json", @"project_path": bad}));
        NSString *artifact = [prefix stringByReplacingOccurrencesOfString:@"/projects/" withString:@"/api/projects/"];
        artifact = [[artifact stringByAppendingString:@"releases/release-aff4effaa09bd66c3a70/artifacts/"] stringByAppendingString:@"release-aff4effaa09bd66c3a70-cad"];
        assert(GAIsReleaseArtifactPath(artifact));
        assert(GAIsReleaseArtifactPath([artifact stringByReplacingOccurrencesOfString:@"-cad" withString:@"-schedule"]));
        for (NSString *bad in @[
            [artifact stringByAppendingString:@"?next=/projects"],
            [artifact stringByAppendingString:@"/more"],
            @"/api/projects/not-a-project/releases/release-1/artifacts/release-1-cad",
            @"/api/projects/122a1ecd-4e1f-4a4e-bf88-13b93299ae1f/release/release-1/artifacts/release-1-cad",
            @"/api/projects/122a1ecd-4e1f-4a4e-bf88-13b93299ae1f/releases/release-1/artifacts/../../private"
        ]) assert(!GAIsReleaseArtifactPath(bad));
    }
}
