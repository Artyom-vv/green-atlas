#pragma once
#import <Foundation/Foundation.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>

// NSURL's resolver may abbreviate /private/var to /var on macOS, even though
// /private/var is the POSIX canonical path. Compare real paths, not that display
// abbreviation. A user-created symlink must still fail, including parent links.
static inline BOOL GALocalDirectPath(NSURL* url) {
    char resolved[PATH_MAX];
    const char* path = url.fileSystemRepresentation;
    return path && realpath(path, resolved) && strcmp(path, resolved) == 0;
}
