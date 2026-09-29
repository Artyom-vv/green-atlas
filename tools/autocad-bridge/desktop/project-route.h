#import <Foundation/Foundation.h>

// Only navigate to a project route emitted by this handoff protocol. Live CAD
// enters layer setup; legacy prepared packages retain the import-review route.
// Do not accept arbitrary local paths, absolute URLs, fragments or extra queries.
static inline NSString *GAHandoffProjectPath(NSDictionary *receipt) {
    id path = receipt[@"project_path"];
    id entry = receipt[@"entry"];
    if (![path isKindOfClass:NSString.class] || ![entry isKindOfClass:NSString.class]) return nil;
    NSString *destination = [entry isEqual:@"Drawing.autocad.json"] ? @"setup" : @"import\\?source=cad";
    NSString *pattern = [NSString stringWithFormat:
        @"^/projects/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/%@$", destination];
    NSRegularExpression *valid = [NSRegularExpression regularExpressionWithPattern:pattern options:0 error:nil];
    NSTextCheckingResult *match = [valid firstMatchInString:path options:0 range:NSMakeRange(0, [path length])];
    return match && NSEqualRanges(match.range, NSMakeRange(0, [path length])) ? path : nil;
}

// A release artifact is a download, never a document to render in WKWebView.
// Validate the route before changing navigation policy; other local URLs keep
// their normal behavior and external hosts remain blocked by the shell.
static inline BOOL GAIsReleaseArtifactPath(NSString *path) {
    if (![path isKindOfClass:NSString.class]) return NO;
    static NSString *pattern = @"^/api/projects/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/releases/[A-Za-z0-9_-]+/artifacts/[A-Za-z0-9_-]+$";
    NSRegularExpression *valid = [NSRegularExpression regularExpressionWithPattern:pattern options:0 error:nil];
    NSTextCheckingResult *match = [valid firstMatchInString:path options:0 range:NSMakeRange(0, path.length)];
    return match && NSEqualRanges(match.range, NSMakeRange(0, path.length));
}
