#import <Foundation/Foundation.h>
#import <CommonCrypto/CommonDigest.h>
#include "contracts.h"

// Идентификатор совпадает с журналом API и позволяет прочитать результат
// передачи, даже если ответ первого POST потерян. Нормализация пути запрещена
static inline NSString *GAHandoffIdentifier(NSString *path) {
    if (!path.isAbsolutePath || ![path isEqual:path.stringByStandardizingPath]) return nil;
    NSData *bytes = [path dataUsingEncoding:NSUTF8StringEncoding];
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(bytes.bytes, (CC_LONG)bytes.length, digest);
    NSMutableString *value = [NSMutableString stringWithCapacity:2 * CC_SHA256_DIGEST_LENGTH];
    for (unsigned char byte : digest) [value appendFormat:@"%02x", byte];
    return value;
}

// Only navigate to a project route emitted by this handoff protocol. Live CAD
// enters layer setup; legacy prepared packages retain the import-review route.
// Do not accept arbitrary local paths, absolute URLs, fragments or extra queries.
static inline NSString *GAHandoffProjectPath(NSDictionary *receipt) {
    id path = receipt[@"project_path"];
    id entry = receipt[@"entry"];
    if (![path isKindOfClass:NSString.class] || ![entry isKindOfClass:NSString.class]) return nil;
    return ga::desktop::handoffRoute([entry UTF8String], [path UTF8String]) ? path : nil;
}

// A release artifact is a download, never a document to render in WKWebView.
// Validate the route before changing navigation policy; other local URLs keep
// their normal behavior and external hosts remain blocked by the shell.
static inline BOOL GAIsReleaseArtifactPath(NSString *path) {
    if (![path isKindOfClass:NSString.class]) return NO;
    return ga::desktop::releaseArtifactRoute([path UTF8String])
        || ga::desktop::renderImageRoute([path UTF8String]);
}
