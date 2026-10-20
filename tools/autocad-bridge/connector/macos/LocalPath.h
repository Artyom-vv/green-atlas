#pragma once
#import <Foundation/Foundation.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>

// Домен локальной передачи: проверяем путь без символических ссылок.
// POSIX-путь сохраняет /private/var; Foundation может иначе представить Unicode.
static inline BOOL GALocalDirectPath(NSURL* url) {
    char resolved[PATH_MAX];
    const char* path = url.fileSystemRepresentation;
    if (!path || !realpath(path, resolved)) return NO;
    if (strcmp(path, resolved) == 0) return YES;
    NSString* requested = [NSString stringWithUTF8String:path];
    NSString* canonical = [NSString stringWithUTF8String:resolved];
    if (!requested || !canonical ||
        ![requested.precomposedStringWithCanonicalMapping
            isEqualToString:canonical.precomposedStringWithCanonicalMapping]) return NO;
    // Эквивалентность Unicode не разрешает ссылку с эквивалентным именем.
    for (NSURL* component = url; component.path.length > 1;
            component = component.URLByDeletingLastPathComponent) {
        NSNumber* symbolicLink = nil;
        if (![component getResourceValue:&symbolicLink forKey:NSURLIsSymbolicLinkKey error:nil]
            || symbolicLink.boolValue) return NO;
    }
    return YES;
}
