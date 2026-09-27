// Developer-only executable: production ticket writer without AutoCAD.
#import <AppKit/AppKit.h>
#include "delivery_ui.h"
#include <cstdio>

int main(int argc, const char* argv[]) {
    @autoreleasepool {
        if (argc != 3) return 2;
        const std::string command = argv[1];
        if (command == "menu") {
            [NSApplication sharedApplication];
            NSApp.mainMenu = [[NSMenu alloc] initWithTitle:@"First"];
            gaDelivery::installMenu(+[]{});
            gaDelivery::installMenu(+[]{});
            if (NSApp.mainMenu.numberOfItems != 1) return 1;
            [NSApp.mainMenu removeItemAtIndex:0];
            gaDelivery::installMenu(+[]{});
            if (NSApp.mainMenu.numberOfItems != 1) return 2;
            NSApp.mainMenu = [[NSMenu alloc] initWithTitle:@"Document"];
            gaDelivery::installMenu(+[]{});
            if (NSApp.mainMenu.numberOfItems != 1) return 3;
            gaDelivery::removeMenu();
            return NSApp.mainMenu.numberOfItems == 0 ? 0 : 4;
        }
        if (command == "inspect") {
            NSURL* url = [[NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[2]] isDirectory:YES]
                URLByAppendingPathComponent:@"Drawing.dxf.green-atlas.geometry.json"];
            NSError* error = nil;
            NSData* data = [NSData dataWithContentsOfURL:url options:0 error:&error];
            id object = data ? [NSJSONSerialization JSONObjectWithData:data options:NSJSONReadingMutableContainers error:&error] : nil;
            NSLog(@"path=%@ resolved=%@ attributes=%@ class=%@ error=%@", url.path,
                url.URLByResolvingSymlinksInPath.path,
                [NSFileManager.defaultManager attributesOfItemAtPath:url.path error:nil], [object class], error);
            return 0;
        }
        if (command == "ticket") {
            const auto result = gaDelivery::writeTicket(argv[2], "0.1.28", "2027",
                {"Missing reference: survey.dwg"});
            if (result.empty()) return 1;
            std::puts(result.c_str());
            return 0;
        }
        if (command == "live-ticket") {
            const auto result = gaDelivery::writeLiveTicket(argv[2], "0.1.38", "2027");
            if (result.empty()) return 1;
            std::puts(result.c_str());
            return 0;
        }
        if (command == "live-issues") {
            for (const auto& issue : gaDelivery::liveProbeIssues(argv[2]))
                std::printf("%s\t%s\n", issue.label.c_str(), issue.value.c_str());
            return 0;
        }
        if (command == "issues") {
            for (const auto& issue : gaDelivery::probeIssues(argv[2])) std::puts(issue.c_str());
            return 0;
        }
        return 2;
    }
}
