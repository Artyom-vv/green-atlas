#import "mac-leave-guard.h"

@implementation GADesktopLeaveGuard {
    ga::desktop::LeaveCoordinator _coordinator;
    NSString *_origin;
    void (^_completion)(BOOL);
    BOOL _runtimeAvailable;
}
- (void)newDocument:(NSString *)origin {
    void (^previous)(BOOL) = _completion;
    _completion = nil;
    _origin = [origin copy];
    _coordinator.newDocument();
    if (previous) previous(NO);
}
- (void)send:(NSDictionary *)event {
    NSData *data = [NSJSONSerialization dataWithJSONObject:event options:0 error:nil];
    NSString *json = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    if (!json) return;
    [self.web evaluateJavaScript:[NSString stringWithFormat:
        @"window.__greenAtlasDesktopReceive && window.__greenAtlasDesktopReceive(%@)", json]
        completionHandler:nil];
}
- (void)runtimeAvailable:(BOOL)available {
    _runtimeAvailable = available;
    if (!_origin.length || !_coordinator.generation()) return;
    if (!available) {
        _coordinator.cancel();
        void (^previous)(BOOL) = _completion; _completion = nil;
        if (previous) previous(NO);
    }
    [self send:@{@"schemaVersion": @1, @"type": available ? @"runtimeRestored" : @"runtimeUnavailable",
        @"origin": _origin, @"documentGeneration": @(_coordinator.generation())}];
}
- (BOOL)documentReady { return _coordinator.isReady(); }
- (BOOL)request:(ga::desktop::Action)action target:(NSString *)target
    completion:(void (^)(BOOL))completion {
    // There is no web draft before the first local document is loaded.
    if (!_origin.length || !self.web.URL || [self.web.URL.absoluteString isEqual:@"about:blank"]) {
        completion(YES); return YES;
    }
    auto request = _coordinator.begin(action, target ? target.UTF8String : "");
    if (!request) { if (self.unavailable) self.unavailable(); return NO; }
    _completion = [completion copy];
    NSMutableDictionary *event = [@{@"schemaVersion": @1, @"type": @"navigationRequest",
        @"origin": _origin, @"documentGeneration": @(request->generation),
        @"requestId": @(request->id), @"action": @(ga::desktop::actionName(action))} mutableCopy];
    if (target) event[@"target"] = target;
    [self send:event];
    return YES;
}
- (void)userContentController:(WKUserContentController *)controller didReceiveScriptMessage:(WKScriptMessage *)message {
    if (!message.frameInfo.mainFrame || ![message.body isKindOfClass:NSDictionary.class]) return;
    NSURLComponents *origin = [NSURLComponents componentsWithString:_origin];
    WKSecurityOrigin *sender = message.frameInfo.securityOrigin;
    if (!origin || ![sender.protocol isEqual:origin.scheme] || ![sender.host isEqual:origin.host]
        || sender.port != origin.port.integerValue) return;
    NSDictionary *event = message.body;
    NSNumber *version = event[@"schemaVersion"];
    if (![version isKindOfClass:NSNumber.class] || CFGetTypeID((__bridge CFTypeRef)version) == CFBooleanGetTypeID()
        || ![version isEqual:@1] || ![event[@"origin"] isEqual:_origin]) return;
    if ([event[@"type"] isEqual:@"ready"]) {
        // Re-attaching the document adapter resets its request correlation.
        // Cancel the old request without discarding the document or its draft.
        _coordinator.cancel();
        void (^previous)(BOOL) = _completion; _completion = nil;
        if (previous) previous(NO);
        _coordinator.ready();
        [self send:@{@"schemaVersion": @1, @"type": @"capabilities", @"origin": _origin,
            @"documentGeneration": @(_coordinator.generation())}];
        [self runtimeAvailable:_runtimeAvailable];
        return;
    }
    if (![event[@"type"] isEqual:@"decision"] || ![event[@"documentGeneration"] isKindOfClass:NSNumber.class]
        || ![event[@"requestId"] isKindOfClass:NSNumber.class] || ![event[@"allow"] isKindOfClass:NSNumber.class]) return;
    if (CFGetTypeID((__bridge CFTypeRef)event[@"allow"]) != CFBooleanGetTypeID()) return;
    for (NSString *key in @[@"documentGeneration", @"requestId"]) {
        if (CFGetTypeID((__bridge CFTypeRef)event[key]) == CFBooleanGetTypeID()) return;
        double value = [event[key] doubleValue];
        if (value < 1 || value > 9007199254740991.0 || value != floor(value)) return;
    }
    const auto generation = [event[@"documentGeneration"] unsignedLongLongValue];
    const auto identifier = [event[@"requestId"] unsignedLongLongValue];
    if (generation != _coordinator.generation() || !_coordinator.pending() || !_completion) return;
    // A stale request in this same document must not dismiss a newer request.
    auto accepted = _coordinator.decide(generation, identifier, [event[@"allow"] boolValue]);
    if (_coordinator.pending()) return;
    void (^completion)(BOOL) = _completion; _completion = nil;
    completion(accepted.has_value());
}
@end
