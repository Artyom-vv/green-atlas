#import "mac-qualification.h"
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

@interface GADesktopQualification ()
@property(readwrite, strong) NSURL *profile;
@property(readwrite, strong) NSURL *receipt;
@property(readwrite) BOOL finished;
@property(readwrite) BOOL passed;
@end

static void qualificationError(NSError **error, NSString *message) {
    if (error) *error = [NSError errorWithDomain:@"ru.green-atlas.qualification" code:1
        userInfo:@{NSLocalizedDescriptionKey:message}];
}

static BOOL plainParents(NSString *path) {
    for (NSString *part = path; ; part = part.stringByDeletingLastPathComponent) {
        struct stat info;
        if (lstat(part.fileSystemRepresentation, &info) != 0 || !S_ISDIR(info.st_mode)) return NO;
        if ([part isEqual:@"/"]) return YES;
        if (!part.length) return NO;
    }
}

GADesktopQualification *GAParseDesktopQualification(int argc, const char *const *argv, NSError **error) {
    BOOL requested = NO;
    for (int index = 1; index < argc; index++) {
        if (strncmp(argv[index], "--qualify", 9) == 0) requested = YES;
    }
    if (!requested) return nil;
    if (argc != 5 || strcmp(argv[1], "--qualify-profile") != 0
        || strcmp(argv[3], "--qualify-receipt") != 0) {
        qualificationError(error, @"Нужны --qualify-profile и --qualify-receipt с новыми абсолютными путями"); return nil;
    }
    NSString *profile = [NSString stringWithUTF8String:argv[2]];
    NSString *receipt = [NSString stringWithUTF8String:argv[4]];
    if (!profile || !receipt || profile.length > 4096 || receipt.length > 4096
        || !profile.isAbsolutePath || ![profile isEqual:profile.stringByStandardizingPath]
        || ![profile.lastPathComponent isEqual:@"RuntimeProfile"]
        || ![receipt isEqual:[profile stringByAppendingPathComponent:@"desktop-qualification.json"]]) {
        qualificationError(error, @"Пути приёмки должны быть однозначными и принадлежать RuntimeProfile"); return nil;
    }
    NSString *root = profile.stringByDeletingLastPathComponent;
    NSString *prefix = @"GA-Mac-Qualification-";
    NSString *name = root.lastPathComponent;
    NSString *session = [name hasPrefix:prefix] ? [name substringFromIndex:prefix.length] : nil;
    NSUUID *identifier = session ? [[NSUUID alloc] initWithUUIDString:session] : nil;
    struct stat info;
    if (!identifier || ![identifier.UUIDString isEqual:session] || !plainParents(root)
        || lstat(root.fileSystemRepresentation, &info) != 0 || info.st_uid != getuid()
        || (info.st_mode & 0777) != 0700) {
        qualificationError(error, @"Нужен собственный приватный корень приёмки установщика без ссылок"); return nil;
    }
    const int directory = open(root.fileSystemRepresentation, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (directory < 0) { qualificationError(error, @"Корень приёмки недоступен"); return nil; }
    BOOL ownedRoot = fstat(directory, &info) == 0 && S_ISDIR(info.st_mode)
        && info.st_uid == getuid() && (info.st_mode & 0777) == 0700;
    const int marker = ownedRoot ? openat(directory, ".green-atlas-installer-qualification.json", O_RDONLY | O_NOFOLLOW | O_CLOEXEC) : -1;
    NSMutableData *bytes = [NSMutableData data];
    BOOL valid = marker >= 0 && fstat(marker, &info) == 0 && S_ISREG(info.st_mode)
        && info.st_uid == getuid() && info.st_nlink == 1 && (info.st_mode & 0777) == 0600
        && info.st_size > 0 && info.st_size <= 16384;
    if (valid) {
        char buffer[4096]; ssize_t count;
        while ((count = read(marker, buffer, sizeof(buffer))) > 0) {
            [bytes appendBytes:buffer length:(NSUInteger)count];
            if (bytes.length > 16384) { valid = NO; break; }
        }
        if (count < 0) valid = NO;
    }
    if (marker >= 0) close(marker);
    id value = valid ? [NSJSONSerialization JSONObjectWithData:bytes options:0 error:nil] : nil;
    NSNumber *uid = [value isKindOfClass:NSDictionary.class] ? value[@"uid"] : nil;
    valid = valid && [value isKindOfClass:NSDictionary.class]
        && [value[@"schema"] isEqual:@"green-atlas.macos-installer-qa-root/1"]
        && [value[@"session_uuid"] isEqual:session]
        && [uid isKindOfClass:NSNumber.class] && CFGetTypeID((__bridge CFTypeRef)uid) != CFBooleanGetTypeID()
        && uid.unsignedLongLongValue == getuid()
        && [@[@"installed", @"updated", @"rolled_back"] containsObject:value[@"phase"]];
    if (!valid || mkdirat(directory, "RuntimeProfile", 0700) != 0) {
        close(directory); qualificationError(error, @"Приёмка не разрешена либо RuntimeProfile уже существует"); return nil;
    }
    close(directory);
    GADesktopQualification *result = [[GADesktopQualification alloc] init];
    result.profile = [NSURL fileURLWithPath:profile isDirectory:YES];
    result.receipt = [NSURL fileURLWithPath:receipt];
    return result;
}

@implementation GADesktopQualification
- (NSDictionary<NSString *, NSString *> *)runtimeEnvironment {
    return @{@"PATH":@"/usr/bin:/bin", @"LANG":@"en_US.UTF-8", @"LC_CTYPE":@"UTF-8",
        @"TMPDIR":[self.profile URLByAppendingPathComponent:@"Temp"].path,
        @"GREEN_ATLAS_LAYER_MODEL_PROVIDER":@"names"};
}
- (BOOL)finish:(NSDictionary *)proof runtimePID:(pid_t)pid runtimeExit:(int)status error:(NSString *)failure {
    if (self.finished) return self.passed;
    self.finished = YES;
    id stopped = proof[@"shutdown_202"];
    self.passed = !failure && pid > 0 && status == 0 && GADesktopQualificationWebProof(proof)
        && [stopped isKindOfClass:NSNumber.class] && CFGetTypeID((__bridge CFTypeRef)stopped) == CFBooleanGetTypeID()
        && [stopped boolValue];
    NSMutableDictionary *receipt = [@{@"schema":@"green-atlas.macos-desktop-qualification/1",
        @"passed":@(self.passed), @"product_qualified":@NO, @"autocad_executed":@NO,
        @"blender_executed":@NO, @"nonpersistent_webview":@YES,
        @"desktop_pid":@(getpid()), @"runtime_pid":@(pid), @"runtime_exit_code":@(status),
        @"profile":self.profile.path, @"checks":proof} mutableCopy];
    if (failure) receipt[@"error"] = failure;
    NSData *bytes = [NSJSONSerialization dataWithJSONObject:receipt options:NSJSONWritingPrettyPrinted error:nil];
    const int output = open(self.receipt.fileSystemRepresentation, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    BOOL written = bytes && output >= 0;
    for (NSUInteger offset = 0; written && offset < bytes.length; ) {
        const ssize_t count = write(output, (const char *)bytes.bytes + offset, bytes.length - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) { written = NO; break; }
        offset += (NSUInteger)count;
    }
    if (output >= 0 && close(output) != 0) written = NO;
    if (!written) self.passed = NO;
    return self.passed;
}
@end

void GAConfigureDesktopQualificationPreferences(void) {
    // Только память текущего процесса. Application domain и plist живого
    // установленного приложения не меняются; настройка предшествует NSApp.
    NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
    NSMutableDictionary *arguments = [[defaults volatileDomainForName:NSArgumentDomain] mutableCopy];
    if (!arguments) arguments = [NSMutableDictionary dictionary];
    arguments[@"NSQuitAlwaysKeepsWindows"] = @NO;
    arguments[@"ApplePersistenceIgnoreState"] = @YES;
    [defaults setVolatileDomain:arguments forName:NSArgumentDomain];
}

NSString *GADesktopQualificationJavaScript(void) {
    // Выполняется в настоящем production WKWebView после загрузки полного Web.
    return @"const end = performance.now() + 15000;"
        "while (performance.now() < end && !(document.readyState === 'complete' && "
        "document.querySelector('#root h1')?.textContent.trim() === 'Проекты')) "
        "{ await new Promise(resolve => setTimeout(resolve, 100)); }"
        "const health = await fetch('/_desktop/health', {cache:'no-store', redirect:'error'});"
        "const h = await health.json();"
        "const projects = await fetch('/api/projects', {cache:'no-store', redirect:'error'});"
        "const p = await projects.json();"
        "return {document_complete:document.readyState === 'complete',"
        "compiled_module_present:!!document.querySelector('script[type=module][src]'),"
        "rendered_project_library:document.querySelector('#root h1')?.textContent.trim() === 'Проекты',"
        "private_health:health.status === 200 && h.service === 'green-atlas-desktop' && h.protocol === 1,"
        "empty_projects:projects.status === 200 && Array.isArray(p) && p.length === 0};";
}

BOOL GADesktopQualificationWebProof(id value) {
    if (![value isKindOfClass:NSDictionary.class]) return NO;
    for (NSString *key in @[@"document_complete", @"compiled_module_present", @"rendered_project_library",
        @"private_health", @"empty_projects"]) {
        id flag = value[key];
        if (![flag isKindOfClass:NSNumber.class] || CFGetTypeID((__bridge CFTypeRef)flag) != CFBooleanGetTypeID()
            || ![flag boolValue]) return NO;
    }
    return YES;
}
