#import <AppKit/AppKit.h>
#import <WebKit/WebKit.h>
#import "mac-download-response.h"
#include <cassert>

@interface DownloadResponseProbe : NSObject <WKNavigationDelegate, WKDownloadDelegate>
@property BOOL done;
@property BOOL guarded;
@property NSInteger status;
@property NSUInteger responses;
@property NSUInteger destinations;
@property NSUInteger cancellations;
@property(copy) NSString *explanation;
@property(strong) WKDownload *download;
@end

@implementation DownloadResponseProbe
- (void)webView:(WKWebView *)webView decidePolicyForNavigationAction:(WKNavigationAction *)action decisionHandler:(void (^)(WKNavigationActionPolicy))completion {
    completion(WKNavigationActionPolicyDownload);
}
- (void)webView:(WKWebView *)webView navigationAction:(WKNavigationAction *)action didBecomeDownload:(WKDownload *)download {
    self.download = download; download.delegate = self;
}
- (void)download:(WKDownload *)download decideDestinationUsingResponse:(NSURLResponse *)response suggestedFilename:(NSString *)filename completionHandler:(void (^)(NSURL *))completion {
    self.responses++; self.status = [(NSHTTPURLResponse *)response statusCode];
    if (self.guarded && GARejectReleaseDownloadResponse(response, ^(NSURL *destination) {
        assert(destination == nil); self.cancellations++; completion(nil);
    }, ^(NSString *explanation) {
        self.explanation = explanation;
    })) { self.done = YES; return; }
    // Reaching this branch means the app would offer a save destination. The
    // probe itself cancels without displaying a panel or creating a file.
    self.destinations++; self.done = YES; completion(nil);
}
@end

static void checkResponsePolicy() {
    NSURL *url = [NSURL URLWithString:@"http://127.0.0.1/artifact"];
    for (NSNumber *status in @[@200, @400, @401, @404, @500]) {
        NSHTTPURLResponse *response = [[NSHTTPURLResponse alloc] initWithURL:url
            statusCode:status.integerValue HTTPVersion:@"HTTP/1.1" headerFields:@{}];
        __block NSUInteger cancellations = 0, notices = 0;
        BOOL refused = GARejectReleaseDownloadResponse(response, ^(NSURL *destination) {
            assert(destination == nil); cancellations++;
        }, ^(NSString *explanation) {
            assert([explanation containsString:status.stringValue]); notices++;
        });
        BOOL expected = status.integerValue != 200;
        assert(refused == expected && cancellations == expected && notices == expected);
    }
    NSURLResponse *nonHTTP = [[NSURLResponse alloc] initWithURL:url MIMEType:nil
        expectedContentLength:0 textEncodingName:nil];
    __block NSUInteger cancellations = 0, notices = 0;
    assert(GARejectReleaseDownloadResponse(nonHTTP, ^(NSURL *destination) {
        assert(destination == nil); cancellations++;
    }, ^(NSString *explanation) {
        assert(explanation.length > 0); notices++;
    }));
    assert(cancellations == 1 && notices == 1);
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        checkResponsePolicy();
        if (argc < 2) { puts("6 download response policy cases passed"); return 0; }
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyProhibited];
        [NSApp finishLaunching];
        NSMutableArray *results = [NSMutableArray array];
        for (NSNumber *guarded in @[@NO, @YES]) {
            for (NSString *name in @[@"ok", @"bad"]) {
                WKWebViewConfiguration *configuration = [[WKWebViewConfiguration alloc] init];
                configuration.websiteDataStore = WKWebsiteDataStore.nonPersistentDataStore;
                WKWebView *web = [[WKWebView alloc] initWithFrame:NSMakeRect(0,0,100,100) configuration:configuration];
                NSWindow *window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0,0,100,100)
                    styleMask:NSWindowStyleMaskBorderless backing:NSBackingStoreBuffered defer:NO];
                window.contentView = web;
                DownloadResponseProbe *probe = [[DownloadResponseProbe alloc] init];
                probe.guarded = guarded.boolValue; web.navigationDelegate = probe;
                [web loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:
                    [NSString stringWithFormat:@"%s/%@", argv[1], name]]]];
                NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:20];
                while (!probe.done && deadline.timeIntervalSinceNow > 0)
                    [[NSRunLoop mainRunLoop] runMode:NSDefaultRunLoopMode
                        beforeDate:[NSDate dateWithTimeIntervalSinceNow:.05]];
                assert(probe.done && probe.responses == 1);
                BOOL error = [name isEqualToString:@"bad"];
                assert(probe.status == (error ? 400 : 200));
                BOOL refused = probe.guarded && error;
                assert(probe.destinations == !refused && probe.cancellations == refused);
                assert((probe.explanation.length > 0) == refused);
                [results addObject:@{@"name":name, @"guarded":guarded,
                    @"status":@(probe.status), @"responses":@(probe.responses),
                    @"destinations":@(probe.destinations), @"cancellations":@(probe.cancellations),
                    @"explanation":probe.explanation ?: @""}];
            }
        }
        NSData *json = [NSJSONSerialization dataWithJSONObject:results
            options:NSJSONWritingPrettyPrinted error:nil];
        fwrite(json.bytes, 1, json.length, stdout); puts("");
    }
}
