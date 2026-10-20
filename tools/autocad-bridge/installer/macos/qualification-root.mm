#import "qualification-root.h"
#include <fcntl.h>
#include <errno.h>
#include <sys/stat.h>
#include <unistd.h>

static NSString *const QualificationMarker = @".green-atlas-installer-qualification.json";
static NSString *const Schema = @"green-atlas.macos-installer-qa-root/1";
static NSString *const Prefix = @"GA-Mac-Qualification-";

static BOOL reject(NSError **error, NSString *message) {
    if (error) *error = [NSError errorWithDomain:@"ru.green-atlas.installer-qualification"
        code:1 userInfo:@{NSLocalizedDescriptionKey:message}];
    return NO;
}

static BOOL validPath(NSURL *root, BOOL existing, NSError **error) {
    NSString *path = root.path;
    if (!root.isFileURL || !path.isAbsolutePath
        || ![path isEqual:root.URLByStandardizingPath.path]
        || ![root.lastPathComponent hasPrefix:Prefix])
        return reject(error, @"Нужен явный абсолютный каталог GA-Mac-Qualification-UUID.");
    NSString *session = [root.lastPathComponent substringFromIndex:Prefix.length];
    if (![[[NSUUID alloc] initWithUUIDString:session].UUIDString isEqual:session])
        return reject(error, @"Имя профиля приёмки должно содержать точный UUID.");
    NSURL *part = existing ? root : root.URLByDeletingLastPathComponent;
    while (part.path.length) {
        struct stat info;
        if (lstat(part.fileSystemRepresentation, &info) != 0 || !S_ISDIR(info.st_mode))
            return reject(error, @"Каталог приёмки или его родитель отсутствует либо содержит ссылку.");
        if ([part.path isEqual:root.path]
            && (info.st_uid != getuid() || (info.st_mode & 0777) != 0700))
            return reject(error, @"Профиль приёмки должен принадлежать текущему пользователю и иметь права 0700.");
        if ([part.path isEqual:@"/"]) break;
        part = part.URLByDeletingLastPathComponent;
    }
    return YES;
}

BOOL GAQualificationChild(NSURL *root, NSURL *child) {
    NSString *prefix = [root.path stringByAppendingString:@"/"];
    if (!(child.isFileURL && [child.path hasPrefix:prefix]
        && [child.path isEqual:child.URLByStandardizingPath.path]
        && [child.path isEqual:child.URLByResolvingSymlinksInPath.path])) return NO;
    // ResolvingSymlinks может оставить отсутствующий leaf неизменённым; проверяем родителей явно.
    NSURL *part = child;
    while (![part.path isEqual:root.path]) {
        struct stat info;
        if (lstat(part.fileSystemRepresentation, &info) == 0) {
            if (S_ISLNK(info.st_mode) || (![part.path isEqual:child.path] && !S_ISDIR(info.st_mode))) return NO;
        } else if (errno != ENOENT) return NO;
        part = part.URLByDeletingLastPathComponent;
    }
    return YES;
}

BOOL GAWriteQualificationRoot(NSURL *root, NSDictionary *state, NSError **error) {
    if (!validPath(root, YES, error)) return NO;
    NSData *data = [NSJSONSerialization dataWithJSONObject:state options:NSJSONWritingSortedKeys error:error];
    if (!data) return NO;
    NSURL *target = [root URLByAppendingPathComponent:QualificationMarker];
    NSURL *temporary = [root URLByAppendingPathComponent:[@".qa-state-" stringByAppendingString:NSUUID.UUID.UUIDString]];
    int handle = open(temporary.fileSystemRepresentation, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (handle < 0) return reject(error, @"Не удалось создать квитанцию профиля приёмки.");
    const unsigned char *bytes = static_cast<const unsigned char *>(data.bytes);
    size_t offset = 0;
    while (offset < data.length) {
        ssize_t written = write(handle, bytes + offset, data.length - offset);
        if (written <= 0) break;
        offset += static_cast<size_t>(written);
    }
    BOOL complete = offset == data.length && fsync(handle) == 0;
    if (close(handle) != 0) complete = NO;
    if (!complete || rename(temporary.fileSystemRepresentation, target.fileSystemRepresentation) != 0) {
        unlink(temporary.fileSystemRepresentation);
        return reject(error, @"Не удалось сохранить квитанцию профиля приёмки.");
    }
    return YES;
}

BOOL GACreateQualificationRoot(NSURL *root, NSError **error) {
    if (!validPath(root, NO, error)) return NO;
    if (mkdir(root.fileSystemRepresentation, 0700) != 0)
        return reject(error, @"Профиль приёмки должен быть новым; существующие каталоги не используются.");
    NSString *session = [root.lastPathComponent substringFromIndex:Prefix.length];
    NSDictionary *state = @{@"schema":Schema, @"uid":@(getuid()), @"session_uuid":session,
        @"phase":@"init", @"backup":[NSNull null]};
    if (GAWriteQualificationRoot(root, state, error)) return YES;
    rmdir(root.fileSystemRepresentation);
    return NO;
}

NSDictionary *GAReadQualificationRoot(NSURL *root, NSError **error) {
    if (!validPath(root, YES, error)) return nil;
    NSURL *marker = [root URLByAppendingPathComponent:QualificationMarker];
    int handle = open(marker.fileSystemRepresentation, O_RDONLY | O_NOFOLLOW | O_NONBLOCK);
    if (handle < 0) { reject(error, @"Нет маркера собственного профиля приёмки."); return nil; }
    struct stat info;
    if (fstat(handle, &info) != 0 || !S_ISREG(info.st_mode) || info.st_uid != getuid() || info.st_nlink != 1
        || (info.st_mode & 0777) != 0600 || info.st_size <= 0 || info.st_size > 65536) {
        close(handle); reject(error, @"Маркер профиля приёмки повреждён или имеет неверные права."); return nil;
    }
    NSMutableData *data = [NSMutableData dataWithLength:static_cast<NSUInteger>(info.st_size)];
    size_t offset = 0;
    while (offset < data.length) {
        ssize_t count = read(handle, static_cast<unsigned char *>(data.mutableBytes) + offset, data.length - offset);
        if (count <= 0) break;
        offset += static_cast<size_t>(count);
    }
    close(handle);
    id state = offset == data.length ? [NSJSONSerialization JSONObjectWithData:data options:0 error:error] : nil;
    NSSet *phases = [NSSet setWithArray:@[@"init", @"installed", @"updated", @"rolled_back", @"uninstalled", @"purged"]];
    NSString *session = [root.lastPathComponent substringFromIndex:Prefix.length];
    if (![state isKindOfClass:NSDictionary.class] || ![state[@"schema"] isEqual:Schema]
        || ![state[@"uid"] isEqual:@(getuid())] || ![state[@"session_uuid"] isEqual:session]
        || ![phases containsObject:state[@"phase"]]
        || !([state[@"backup"] isKindOfClass:NSString.class] || state[@"backup"] == NSNull.null)) {
        reject(error, @"Маркер не принадлежит ожидаемому профилю приёмки."); return nil;
    }
    return state;
}
