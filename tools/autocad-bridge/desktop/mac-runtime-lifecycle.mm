#import "mac-runtime-lifecycle.h"
#include <fcntl.h>

// Завершение ограничено временем, но не снимает блокировку профиля и не посылает
// сигналы посторонним процессам. Истёкший срок возвращает управление владельцу окна.
static constexpr NSTimeInterval kRuntimeStopPollSeconds = 0.05;
static constexpr NSUInteger kPreviousRuntimeLogCount = 5;

void GAStopRuntime(NSTask *task, NSTimeInterval timeout, void (^completed)(BOOL)) {
    if (!task.running) {
        dispatch_async(dispatch_get_main_queue(), ^{ completed(YES); });
        return;
    }
    [task terminate];
    const NSTimeInterval deadline = NSProcessInfo.processInfo.systemUptime + timeout;
    dispatch_source_t timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, dispatch_get_main_queue());
    dispatch_source_set_timer(timer, DISPATCH_TIME_NOW, kRuntimeStopPollSeconds * NSEC_PER_SEC, 0);
    dispatch_source_set_event_handler(timer, ^{
        const BOOL stopped = !task.running;
        if (!stopped && NSProcessInfo.processInfo.systemUptime < deadline) return;
        dispatch_source_cancel(timer);
        completed(stopped);
    });
    // Разрываем связь timer → handler → timer после единственного результата.
    dispatch_source_set_cancel_handler(timer, ^{ dispatch_source_set_event_handler(timer, nil); });
    dispatch_resume(timer);
}

NSFileHandle *GARuntimeExitLog(NSFileHandle *log) {
    // Дескриптор остаётся у журнала своего запуска после закрытия и ротации.
    const int descriptor = fcntl(log.fileDescriptor, F_DUPFD_CLOEXEC, 0);
    return descriptor < 0 ? nil : [[NSFileHandle alloc] initWithFileDescriptor:descriptor closeOnDealloc:YES];
}

void GARecordRuntimeExit(NSFileHandle *log, NSTask *task, BOOL expectedClosing, BOOL startupTimedOut) {
    if (!log || task.running) return;
    NSDictionary *event = @{
        @"event": @"runtime_exit",
        @"desktop_pid": @(NSProcessInfo.processInfo.processIdentifier),
        @"runtime_pid": @(task.processIdentifier),
        @"termination_reason": task.terminationReason == NSTaskTerminationReasonExit ? @"exit" : @"signal",
        @"termination_status": @(task.terminationStatus),
        @"expected_closing": @(expectedClosing),
        @"startup_timeout": @(startupTimedOut),
    };
    NSMutableData *line = [[NSJSONSerialization dataWithJSONObject:event options:0 error:nil] mutableCopy];
    [line appendData:[@"\n" dataUsingEncoding:NSUTF8StringEncoding]];
    @try { [log writeData:line]; }
    @catch (NSException *exception) {
        // Сбой диагностики не меняет завершение или восстановление приложения.
        NSLog(@"Green Atlas: не удалось записать причину завершения runtime");
    }
}

static BOOL ownedRuntimeLog(NSURL *url) {
    NSString *name = url.lastPathComponent;
    NSString *prefix = @"desktop-runtime-";
    if (![name hasPrefix:prefix] || ![name hasSuffix:@".log"]) return NO;
    NSString *identifier = [name substringWithRange:NSMakeRange(prefix.length, name.length - prefix.length - 4)];
    NSDictionary *attributes = [NSFileManager.defaultManager attributesOfItemAtPath:url.path error:nil];
    return [[NSUUID alloc] initWithUUIDString:identifier] && [attributes[NSFileType] isEqual:NSFileTypeRegular];
}

NSFileHandle *GAOpenRuntimeLog(NSURL *profile, NSError **error) {
    NSFileManager *manager = NSFileManager.defaultManager;
    NSURL *current = [profile URLByAppendingPathComponent:@"desktop-runtime.log"];
    NSURL *previous = [profile URLByAppendingPathComponent:@"RuntimeLogs" isDirectory:YES];
    if (![manager createDirectoryAtURL:previous withIntermediateDirectories:YES
        attributes:@{NSFilePosixPermissions:@0700} error:error]) return nil;
    if ([manager fileExistsAtPath:current.path]) {
        NSURL *saved = [previous URLByAppendingPathComponent:
            [NSString stringWithFormat:@"desktop-runtime-%@.log", NSUUID.UUID.UUIDString]];
        if (![manager moveItemAtURL:current toURL:saved error:error]) return nil;
    }
    if (![manager createFileAtPath:current.path contents:nil attributes:@{NSFilePosixPermissions:@0600}]) {
        if (error) *error = [NSError errorWithDomain:NSCocoaErrorDomain code:NSFileWriteUnknownError
            userInfo:@{NSFilePathErrorKey:current.path}];
        return nil;
    }
    NSFileHandle *log = [NSFileHandle fileHandleForWritingToURL:current error:error];
    if (!log) return nil;
    NSArray<NSURL *> *logs = [[manager contentsOfDirectoryAtURL:previous
        includingPropertiesForKeys:@[NSURLContentModificationDateKey] options:0 error:nil]
        filteredArrayUsingPredicate:[NSPredicate predicateWithBlock:^BOOL(NSURL *url, NSDictionary *bindings) {
            return ownedRuntimeLog(url);
        }]];
    logs = [logs sortedArrayUsingComparator:^NSComparisonResult(NSURL *a, NSURL *b) {
        NSDate *first, *second;
        [a getResourceValue:&first forKey:NSURLContentModificationDateKey error:nil];
        [b getResourceValue:&second forKey:NSURLContentModificationDateKey error:nil];
        return [second ?: NSDate.distantPast compare:first ?: NSDate.distantPast];
    }];
    for (NSUInteger index = kPreviousRuntimeLogCount; index < logs.count; index++)
        [manager removeItemAtURL:logs[index] error:nil];
    return log;
}
