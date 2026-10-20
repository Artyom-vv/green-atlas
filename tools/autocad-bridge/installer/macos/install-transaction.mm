#import "install-transaction.h"
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include <algorithm>
#include <cerrno>

static NSString *const JournalName = @".green-atlas-install-transaction.plist";
#ifdef GA_INSTALL_TRANSACTION_TESTING
void (*GAInstallTransactionHook)(NSString *) = nullptr;
#endif
static void observe(NSString *event) {
#ifdef GA_INSTALL_TRANSACTION_TESTING
    if (GAInstallTransactionHook) GAInstallTransactionHook(event);
#endif
}
static BOOL fail(NSError **error, NSString *message) {
    if (error) *error = [NSError errorWithDomain:@"ru.green-atlas.install-transaction" code:1
        userInfo:@{NSLocalizedDescriptionKey:message}];
    return NO;
}
static BOOL safeParent(NSURL *parent, NSError **error) {
    if (![parent.path isEqual:parent.URLByStandardizingPath.path]
        || ![parent.path isEqual:parent.URLByResolvingSymlinksInPath.path])
        return fail(error, @"Каталог установки содержит символическую ссылку. Файлы не изменены.");
    if (![NSFileManager.defaultManager createDirectoryAtURL:parent withIntermediateDirectories:YES attributes:nil error:error]) return NO;
    struct stat s;
    return (lstat(parent.fileSystemRepresentation, &s) == 0 && S_ISDIR(s.st_mode) && s.st_uid == getuid())
        || fail(error, @"Каталог установки не принадлежит текущему пользователю.");
}
static NSDictionary *identity(NSURL *path) {
    struct stat s;
    if (lstat(path.fileSystemRepresentation, &s) != 0) return nil;
    if (!S_ISDIR(s.st_mode) || s.st_uid != getuid()
        || ![path.path isEqual:path.URLByResolvingSymlinksInPath.path]) return nil;
    return @{@"device":@(s.st_dev), @"inode":@(s.st_ino),
        @"birth_seconds":@(s.st_birthtimespec.tv_sec), @"birth_nanoseconds":@(s.st_birthtimespec.tv_nsec)};
}
static BOOL exists(NSURL *path) {
    struct stat s;
    return lstat(path.fileSystemRepresentation, &s) == 0 || errno != ENOENT;
}
static BOOL same(NSURL *path, id expected) {
    return [expected isKindOfClass:NSDictionary.class] && [identity(path) isEqual:expected];
}
static BOOL syncDirectory(NSURL *directory, NSError **error) {
    int fd = open(directory.fileSystemRepresentation, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return fail(error, @"Не удалось открыть каталог установки для фиксации обновления.");
    BOOL ok = fsync(fd) == 0;
    close(fd);
    return ok || fail(error, @"Не удалось зафиксировать каталог установки.");
}
// До commit сбрасываются и содержимое новых файлов, и каталоги payload.
static BOOL syncPayload(NSURL *payload, NSError **error) {
    NSMutableArray<NSURL *> *directories = [NSMutableArray arrayWithObject:payload];
    __block NSError *enumerationError = nil;
    NSDirectoryEnumerator *enumerator = [NSFileManager.defaultManager enumeratorAtURL:payload
        includingPropertiesForKeys:nil options:0 errorHandler:^BOOL(NSURL *url, NSError *cause) { enumerationError = cause; return NO; }];
    if (!enumerator) return fail(error, @"Не удалось перечислить подготовленный пакет.");
    for (NSURL *path in enumerator) {
        struct stat s;
        if (lstat(path.fileSystemRepresentation, &s) != 0) return fail(error, @"Не удалось прочитать подготовленный пакет.");
        if (S_ISLNK(s.st_mode)) { [enumerator skipDescendants]; continue; }
        if (S_ISDIR(s.st_mode)) { [directories addObject:path]; continue; }
        if (!S_ISREG(s.st_mode)) return fail(error, @"В программном пакете находится неподдерживаемый файл.");
        int fd = open(path.fileSystemRepresentation, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
        if (fd < 0) return fail(error, @"Не удалось открыть подготовленный файл.");
        BOOL ok = fsync(fd) == 0; close(fd);
        if (!ok) return fail(error, @"Не удалось зафиксировать подготовленный файл.");
    }
    if (enumerationError) { if (error) *error = enumerationError; return NO; }
    for (NSURL *directory in directories.reverseObjectEnumerator)
        if (!syncDirectory(directory, error)) return NO;
    return YES;
}
static BOOL writeJournal(NSURL *path, NSDictionary *journal, NSError **error) {
    NSData *data = [NSPropertyListSerialization dataWithPropertyList:journal format:NSPropertyListBinaryFormat_v1_0 options:0 error:error];
    if (!data) return NO;
    NSURL *temporary = [path.URLByDeletingLastPathComponent URLByAppendingPathComponent:
        [@".green-atlas-journal-" stringByAppendingString:NSUUID.UUID.UUIDString]];
    int fd = open(temporary.fileSystemRepresentation, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return fail(error, @"Не удалось создать журнал обновления.");
    const char *bytes = static_cast<const char *>(data.bytes);
    size_t remaining = data.length;
    while (remaining) {
        ssize_t n = write(fd, bytes, remaining);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) break;
        bytes += n; remaining -= n;
    }
    BOOL ok = remaining == 0 && fsync(fd) == 0;
    close(fd);
    if (ok) ok = rename(temporary.fileSystemRepresentation, path.fileSystemRepresentation) == 0;
    if (!ok) { unlink(temporary.fileSystemRepresentation); return fail(error, @"Не удалось записать журнал обновления."); }
    return syncDirectory(path.URLByDeletingLastPathComponent, error);
}
static BOOL removeExact(NSURL *path, id expected, NSError **error) {
    if (!exists(path)) return YES;
    if (!same(path, expected)) return fail(error, @"Состав установки изменился. Старые файлы сохранены для восстановления.");
    if (![NSFileManager.defaultManager removeItemAtURL:path error:error]) return NO;
    return syncDirectory(path.URLByDeletingLastPathComponent, error);
}
static BOOL moveExact(NSURL *from, NSURL *to, id expected, NSError **error) {
    if (!same(from, expected) || exists(to)) return fail(error, @"Пути обновления изменились. Замена остановлена.");
    if (rename(from.fileSystemRepresentation, to.fileSystemRepresentation) != 0)
        return fail(error, @"Не удалось переместить программный пакет.");
    return syncDirectory(from.URLByDeletingLastPathComponent, error)
        && ([from.URLByDeletingLastPathComponent.path isEqual:to.URLByDeletingLastPathComponent.path]
            || syncDirectory(to.URLByDeletingLastPathComponent, error));
}
static BOOL validPolicy(const GAInstallItem &item) {
    return ([item.name isEqual:@"GreenAtlasBridge.bundle"] && [item.prefix isEqual:@".green-atlas"])
        || ([item.name isEqual:@"Green Atlas.app"] && [item.prefix isEqual:@".green-atlas-launcher"]);
}
static NSURL *child(NSURL *parent, NSString *name) { return [parent URLByAppendingPathComponent:name]; }
static BOOL validJournal(NSDictionary *journal, const std::vector<GAInstallItem> &items, NSError **error) {
    if (![journal isKindOfClass:NSDictionary.class] || ![journal[@"schema"] isEqual:@1]
        || ![@[@"prepared", @"committed"] containsObject:journal[@"phase"]]
        || ![journal[@"id"] isKindOfClass:NSString.class]
        || ![[NSUUID alloc] initWithUUIDString:journal[@"id"]]
        || ![journal[@"items"] isKindOfClass:NSArray.class]
        || [journal[@"items"] count] != items.size()) return fail(error, @"Журнал обновления повреждён. Автоматическое удаление остановлено.");
    for (size_t i = 0; i < items.size(); ++i) {
        NSDictionary *row = journal[@"items"][i]; const auto &item = items[i];
        if (![row isKindOfClass:NSDictionary.class] || ![row[@"parent"] isEqual:item.parent.path]
            || ![row[@"name"] isEqual:item.name]
            || ![row[@"stage"] isEqual:[NSString stringWithFormat:@"%@-incoming-%@", item.prefix, journal[@"id"]]]
            || ![row[@"previous"] isEqual:[NSString stringWithFormat:@"%@-previous-%@", item.prefix, journal[@"id"]]])
            return fail(error, @"Журнал не соответствует каталогам установки. Файлы не удалены.");
        for (NSString *key in @[@"old", @"stage_identity", @"new"])
            if (row[key] && (![row[key] isKindOfClass:NSDictionary.class]
                || ![row[key][@"device"] isKindOfClass:NSNumber.class]
                || ![row[key][@"inode"] isKindOfClass:NSNumber.class]
                || ![row[key][@"birth_seconds"] isKindOfClass:NSNumber.class]
                || ![row[key][@"birth_nanoseconds"] isKindOfClass:NSNumber.class]))
                return fail(error, @"Журнал содержит некорректную идентичность файлов.");
    }
    return YES;
}
static BOOL recover(NSDictionary *journal, const std::vector<GAInstallItem> &items, NSURL *journalPath, NSError **error, BOOL *cleanupPending = nullptr) {
    if (cleanupPending) *cleanupPending = NO;
    if (!validJournal(journal, items, error)) return NO;
    BOOL committed = [journal[@"phase"] isEqual:@"committed"];
    // После commit новый пакет больше не откатывается из-за ошибки очистки.
    if (committed) for (size_t i = 0; i < items.size(); ++i) {
        const auto &item = items[i]; NSDictionary *row = journal[@"items"][i];
        NSURL *target = child(item.parent, item.name);
        if (!same(target, row[@"new"]) || !item.validate(target, error))
            return fail(error, @"Новая установка изменилась после обновления. Предыдущая версия сохранена.");
    }
    if (committed && cleanupPending) *cleanupPending = YES;
    for (size_t remaining = items.size(); remaining > 0; --remaining) {
        size_t i = remaining - 1; const auto &item = items[i]; NSDictionary *row = journal[@"items"][i];
        if (!safeParent(item.parent, error)) return NO;
        NSURL *target = child(item.parent, item.name), *previous = child(item.parent, row[@"previous"]);
        NSURL *stage = child(item.parent, row[@"stage"]);
        if (committed) {
            observe([NSString stringWithFormat:@"before-cleanup-%zu", i]);
            if (exists(previous) && (!same(previous, row[@"old"])
                || !item.disposable(previous, error))) return fail(error, @"Предыдущая сборка содержит неизвестные файлы. Установка завершена, очистка отложена.");
            if (!removeExact(previous, row[@"old"], error)) return NO;
        } else if (row[@"old"]) {
            if (exists(previous)) {
                if (!same(previous, row[@"old"])) return fail(error, @"Предыдущая установка изменилась. Восстановление остановлено.");
                if (exists(target) && !removeExact(target, row[@"new"], error)) return NO;
                if (!moveExact(previous, target, row[@"old"], error)) return NO;
            } else if (!same(target, row[@"old"])) return fail(error, @"Предыдущая установка не найдена. Журнал сохранён.");
        } else if (exists(target) && !removeExact(target, row[@"new"], error)) return NO;
        if (!removeExact(stage, row[@"stage_identity"], error)) return NO;
    }
    if (unlink(journalPath.fileSystemRepresentation) != 0) return fail(error, @"Не удалось завершить журнал обновления.");
    BOOL completed = syncDirectory(journalPath.URLByDeletingLastPathComponent, error);
    if (completed && cleanupPending) *cleanupPending = NO;
    return completed;
}

BOOL GAInstallManagedItems(const std::vector<GAInstallItem> &items, NSError **error, BOOL *cleanupPending) {
    if (cleanupPending) *cleanupPending = NO;
    if (items.empty() || items.size() > 2) return fail(error, @"Некорректный набор программных компонентов.");
    std::vector<NSURL *> parents;
    for (const auto &item : items) {
        if (!validPolicy(item) || !item.owned || !item.validate || !item.disposable || !safeParent(item.parent, error)) return NO;
        if (std::none_of(parents.begin(), parents.end(), [&](NSURL *p) { return [p.path isEqual:item.parent.path]; })) parents.push_back(item.parent);
    }
    std::sort(parents.begin(), parents.end(), [](NSURL *a, NSURL *b) { return [a.path compare:b.path] == NSOrderedAscending; });
    std::vector<int> locks;
    struct Unlock { std::vector<int> &fds; ~Unlock() { for (int fd : fds) { flock(fd, LOCK_UN); close(fd); } } } unlock{locks};
    for (NSURL *parent : parents) {
        NSURL *lock = child(parent, @".green-atlas-install.lock");
        int fd = open(lock.fileSystemRepresentation, O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
        struct stat s;
        if (fd < 0) return fail(error, @"Не удалось открыть блокировку установщика.");
        if (fstat(fd, &s) != 0 || !S_ISREG(s.st_mode) || s.st_uid != getuid() || flock(fd, LOCK_EX | LOCK_NB) != 0) {
            close(fd); return fail(error, @"Другой установщик уже работает с этим каталогом.");
        }
        locks.push_back(fd);
    }
    NSURL *journalPath = child(items[0].parent, JournalName);
    if (exists(journalPath)) {
        struct stat s;
        if (lstat(journalPath.fileSystemRepresentation, &s) != 0 || !S_ISREG(s.st_mode) || s.st_uid != getuid() || s.st_size > 65536)
            return fail(error, @"Журнал установки является посторонним файлом.");
        NSData *data = [NSData dataWithContentsOfURL:journalPath options:0 error:error];
        NSDictionary *journal = data ? [NSPropertyListSerialization propertyListWithData:data options:NSPropertyListImmutable format:nil error:error] : nil;
        BOOL pending = NO;
        if (!journal || !recover(journal, items, journalPath, error, &pending)) {
            if (pending) {
                if (cleanupPending) *cleanupPending = YES;
                NSString *detail = error ? (*error).localizedDescription : nil;
                return fail(error, [@"Предыдущее обновление установлено, но его очистка ещё не завершена. Новое обновление не начато. " stringByAppendingString:detail ?: @""]);
            }
            return NO;
        }
    }
    NSString *transaction = NSUUID.UUID.UUIDString;
    NSMutableArray *rows = [NSMutableArray array];
    for (const auto &item : items) {
        if (!item.validate(item.source, error)) return NO;
        NSURL *target = child(item.parent, item.name); NSDictionary *old = identity(target);
        if (exists(target) && (!old || !item.owned(target))) return fail(error, @"На месте Green Atlas находится посторонний файл. Он не изменён.");
        if (old && !item.disposable(target, error)) return NO;
        NSMutableDictionary *row = [@{@"parent":item.parent.path, @"name":item.name,
            @"stage":[NSString stringWithFormat:@"%@-incoming-%@", item.prefix, transaction],
            @"previous":[NSString stringWithFormat:@"%@-previous-%@", item.prefix, transaction]} mutableCopy];
        if (old) row[@"old"] = old;
        [rows addObject:row];
    }
    NSMutableDictionary *journal = [@{@"schema":@1, @"id":transaction, @"phase":@"prepared", @"items":rows} mutableCopy];
    // До копирования больших payload журнал уже связывает точные stage-каталоги.
    for (size_t i = 0; i < items.size(); ++i) {
        NSURL *stage = child(items[i].parent, rows[i][@"stage"]);
        if (mkdir(stage.fileSystemRepresentation, 0700) != 0) return fail(error, @"Не удалось подготовить каталог обновления.");
        rows[i][@"stage_identity"] = identity(stage);
    }
    if (!writeJournal(journalPath, journal, error)) return NO;
    BOOL ready = YES;
    for (size_t i = 0; i < items.size() && ready; ++i) {
        const auto &item = items[i]; NSURL *stage = child(item.parent, rows[i][@"stage"]);
        NSURL *payload = child(stage, item.name);
        ready = [NSFileManager.defaultManager copyItemAtURL:item.source toURL:payload error:error]
            && item.validate(payload, error) && syncPayload(payload, error) && syncDirectory(stage, error);
        if (ready) {
            NSDictionary *newIdentity = identity(payload);
            ready = newIdentity != nil || fail(error, @"Подготовленный пакет содержит недопустимую ссылку.");
            if (ready) rows[i][@"new"] = newIdentity;
        }
    }
    if (ready) ready = writeJournal(journalPath, journal, error);
    if (ready) observe(@"after-prepared");
    for (size_t i = 0; i < items.size() && ready; ++i) {
        const auto &item = items[i]; NSDictionary *row = rows[i];
        NSURL *target = child(item.parent, item.name), *previous = child(item.parent, row[@"previous"]);
        NSURL *payload = child(child(item.parent, row[@"stage"]), item.name);
        if (row[@"old"]) ready = moveExact(target, previous, row[@"old"], error);
        if (ready) ready = moveExact(payload, target, row[@"new"], error);
        if (ready) observe([NSString stringWithFormat:@"after-place-%zu", i]);
    }
    if (ready) for (size_t i = 0; i < items.size() && ready; ++i)
        ready = (same(child(items[i].parent, items[i].name), rows[i][@"new"])
            || fail(error, @"Новая установка изменилась до завершения проверки."))
            && items[i].validate(child(items[i].parent, items[i].name), error);
    if (!ready) {
        NSError *cause = error ? *error : nil, *restore = nil;
        if (!recover(journal, items, journalPath, &restore)) { if (error) *error = restore; }
        else if (error) *error = cause;
        return NO;
    }
    journal[@"phase"] = @"committed";
    if (!writeJournal(journalPath, journal, error)) return NO;
    observe(@"after-commit");
    BOOL pending = NO;
    BOOL completed = recover(journal, items, journalPath, error, &pending);
    if (pending) { if (cleanupPending) *cleanupPending = YES; return YES; }
    return completed;
}
