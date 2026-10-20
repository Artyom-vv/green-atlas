#import <WebKit/WebKit.h>
#include "contracts.h"

// The shell correlates messages; the web application owns the leave policy.
@interface GADesktopLeaveGuard : NSObject <WKScriptMessageHandler>
@property(weak) WKWebView *web;
@property(copy) void (^unavailable)(void);
- (void)newDocument:(NSString *)origin;
- (void)runtimeAvailable:(BOOL)available;
- (BOOL)documentReady;
- (BOOL)request:(ga::desktop::Action)action target:(NSString *)target
    completion:(void (^)(BOOL allowed))completion;
@end
