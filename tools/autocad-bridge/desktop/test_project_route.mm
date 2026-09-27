#import "project-route.h"
#include <cassert>

int main() {
    @autoreleasepool {
        NSString *prefix = @"/projects/122a1ecd-4e1f-4a4e-bf88-13b93299ae1f/";
        NSString *setup = [prefix stringByAppendingString:@"setup"];
        NSString *legacy = [prefix stringByAppendingString:@"import?source=cad"];
        assert([GAHandoffProjectPath(@{@"entry": @"Drawing.autocad.json", @"project_path": setup}) isEqual:setup]);
        assert([GAHandoffProjectPath(@{@"entry": @"Drawing.dxf", @"project_path": legacy}) isEqual:legacy]);
        assert(!GAHandoffProjectPath(@{@"entry": @"Drawing.autocad.json", @"project_path": legacy}));
        assert(!GAHandoffProjectPath(@{@"entry": @"Drawing.dxf", @"project_path": setup}));
        assert(!GAHandoffProjectPath(@{}));
        assert(!GAHandoffProjectPath(@{@"entry": @"Drawing.autocad.json", @"project_path": @42}));
        for (NSString *bad in @[
            [@"https://example.com" stringByAppendingString:setup],
            [setup stringByAppendingString:@"?redirect=https://example.com"],
            [setup stringByAppendingString:@"#fragment"],
            [setup stringByAppendingString:@"\n"],
            @"/projects/------------------------------------/setup",
            @"/projects/122a1ecd-4e1f-4a4e-bf88-13b93299ae1f/../setup"
        ]) assert(!GAHandoffProjectPath(@{@"entry": @"Drawing.autocad.json", @"project_path": bad}));
    }
}
