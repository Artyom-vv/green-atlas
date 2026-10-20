#import "mac-leave-guard.h"
#include <cassert>

@interface TestWeb : NSObject
@property(strong) NSURL *URL;
@property(copy) NSString *script;
@property(strong) NSMutableArray<NSString *> *scripts;
@end
@implementation TestWeb
- (void)evaluateJavaScript:(NSString *)script completionHandler:(id)completion {
    self.script = script;
    if (!self.scripts) self.scripts = [NSMutableArray array];
    [self.scripts addObject:script];
}
@end
@interface TestOrigin : NSObject
@property(copy) NSString *protocol;
@property(copy) NSString *host;
@property NSInteger port;
@end
@implementation TestOrigin @end
@interface TestFrame : NSObject
@property(getter=isMainFrame) BOOL mainFrame;
@property(strong) TestOrigin *securityOrigin;
@end
@implementation TestFrame @end
@interface TestMessage : NSObject
@property(strong) TestFrame *frameInfo;
@property(strong) NSDictionary *body;
@end
@implementation TestMessage @end

int main() {
    @autoreleasepool {
        WKUserContentController *controller = [[WKUserContentController alloc] init];
        NSString *origin = @"http://127.0.0.1:8123";
        TestWeb *web = [[TestWeb alloc] init]; web.URL = [NSURL URLWithString:[origin stringByAppendingString:@"/projects"]];
        GADesktopLeaveGuard *guard = [[GADesktopLeaveGuard alloc] init]; guard.web = (WKWebView *)web;
        TestMessage *message = [[TestMessage alloc] init];
        message.frameInfo = [[TestFrame alloc] init]; message.frameInfo.mainFrame = YES;
        message.frameInfo.securityOrigin = [[TestOrigin alloc] init];
        message.frameInfo.securityOrigin.protocol = @"http"; message.frameInfo.securityOrigin.host = @"127.0.0.1";
        message.frameInfo.securityOrigin.port = 8123;
        __block int calls = 0; __block BOOL allowed = YES;
        void (^completion)(BOOL) = ^(BOOL value) { ++calls; allowed = value; };
        [guard newDocument:origin];
        assert(![guard request:ga::desktop::Action::close target:nil completion:completion]);
        message.body = @{@"schemaVersion":@1, @"type":@"ready", @"origin":origin};
        [guard userContentController:controller didReceiveScriptMessage:(WKScriptMessage *)message];
        assert([web.scripts[web.scripts.count - 2] containsString:@"capabilities"]);
        assert([web.script containsString:@"runtimeUnavailable"] && [web.script containsString:@"documentGeneration"]);
        assert([guard request:ga::desktop::Action::close target:nil completion:completion]);
        assert(![guard request:ga::desktop::Action::quit target:nil completion:completion]);
        message.body = @{@"schemaVersion":@1, @"type":@"decision", @"origin":origin,
            @"documentGeneration":@2, @"requestId":@1, @"allow":@YES};
        [guard userContentController:controller didReceiveScriptMessage:(WKScriptMessage *)message]; assert(calls == 0);
        message.body = @{@"schemaVersion":@1, @"type":@"decision", @"origin":origin,
            @"documentGeneration":@YES, @"requestId":@1, @"allow":@YES};
        [guard userContentController:controller didReceiveScriptMessage:(WKScriptMessage *)message]; assert(calls == 0);
        message.body = @{@"schemaVersion":@1, @"type":@"decision", @"origin":origin,
            @"documentGeneration":@1, @"requestId":@1, @"allow":@YES};
        message.frameInfo.mainFrame = NO;
        [guard userContentController:controller didReceiveScriptMessage:(WKScriptMessage *)message]; assert(calls == 0);
        message.frameInfo.mainFrame = YES; message.frameInfo.securityOrigin.port = 8124;
        [guard userContentController:controller didReceiveScriptMessage:(WKScriptMessage *)message]; assert(calls == 0);
        message.frameInfo.securityOrigin.port = 8123;
        [guard userContentController:controller didReceiveScriptMessage:(WKScriptMessage *)message]; assert(calls == 1 && allowed);
        [guard userContentController:controller didReceiveScriptMessage:(WKScriptMessage *)message]; assert(calls == 1);
        assert([guard request:ga::desktop::Action::handoff target:@"/projects" completion:completion]);
        [guard newDocument:origin]; assert(calls == 2 && !allowed);
        message.body = @{@"schemaVersion":@1, @"type":@"ready", @"origin":origin};
        [guard userContentController:controller didReceiveScriptMessage:(WKScriptMessage *)message];
        assert([guard request:ga::desktop::Action::quit target:nil completion:completion]);
        message.body = @{@"schemaVersion":@1, @"type":@"decision", @"origin":origin,
            @"documentGeneration":@2, @"requestId":@3, @"allow":@NO};
        [guard userContentController:controller didReceiveScriptMessage:(WKScriptMessage *)message]; assert(calls == 3 && !allowed);
        assert([guard request:ga::desktop::Action::close target:nil completion:completion]);
        [guard runtimeAvailable:NO]; assert(calls == 4 && !allowed);
        assert([web.script containsString:@"runtimeUnavailable"] && [web.script containsString:@"\"documentGeneration\":2"]);
        [guard runtimeAvailable:YES];
        assert([web.script containsString:@"runtimeRestored"] && [web.script containsString:@"\"documentGeneration\":2"]);
        message.body = @{@"schemaVersion":@1, @"type":@"ready", @"origin":origin};
        [guard userContentController:controller didReceiveScriptMessage:(WKScriptMessage *)message];
        assert([web.script containsString:@"runtimeRestored"] && [web.script containsString:@"\"documentGeneration\":2"]);
        assert([guard request:ga::desktop::Action::quit target:nil completion:completion]);
        message.body = @{@"schemaVersion":@1, @"type":@"decision", @"origin":origin,
            @"documentGeneration":@2, @"requestId":@4, @"allow":@YES};
        [guard userContentController:controller didReceiveScriptMessage:(WKScriptMessage *)message]; assert(calls == 4);
        message.body = @{@"schemaVersion":@1, @"type":@"decision", @"origin":origin,
            @"documentGeneration":@2, @"requestId":@5, @"allow":@YES};
        [guard userContentController:controller didReceiveScriptMessage:(WKScriptMessage *)message]; assert(calls == 5 && allowed);
        assert([guard request:ga::desktop::Action::close target:nil completion:completion]);
        message.body = @{@"schemaVersion":@1, @"type":@"ready", @"origin":origin};
        [guard userContentController:controller didReceiveScriptMessage:(WKScriptMessage *)message];
        assert(calls == 6 && !allowed && [guard documentReady]);
        assert([web.script containsString:@"\"documentGeneration\":2"]);
        assert([guard request:ga::desktop::Action::quit target:nil completion:completion]);
        message.body = @{@"schemaVersion":@1, @"type":@"decision", @"origin":origin,
            @"documentGeneration":@2, @"requestId":@6, @"allow":@YES};
        [guard userContentController:controller didReceiveScriptMessage:(WKScriptMessage *)message]; assert(calls == 6);
        message.body = @{@"schemaVersion":@1, @"type":@"decision", @"origin":origin,
            @"documentGeneration":@2, @"requestId":@7, @"allow":@YES};
        [guard userContentController:controller didReceiveScriptMessage:(WKScriptMessage *)message]; assert(calls == 7 && allowed);
    }
}
