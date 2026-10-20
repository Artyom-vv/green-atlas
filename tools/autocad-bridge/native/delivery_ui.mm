// Домен передачи AutoCAD: меню, подтверждения, файлы и запуск локального приложения.
#import <AppKit/AppKit.h>
#import <QuartzCore/QuartzCore.h>
#import <CommonCrypto/CommonDigest.h>
#include <dlfcn.h>
#include <chrono>
#include "delivery_ui.h"
#import "../connector/macos/LocalPath.h"

static NSString* text(const std::string& value) { return [NSString stringWithUTF8String:value.c_str()] ?: @""; }
static void (*openCommand)() = nullptr;
@interface GAOpenMenuTarget : NSObject
- (void)open:(id)sender;
@end
@implementation GAOpenMenuTarget
- (void)open:(id)sender { if (openCommand) openCommand(); }
@end
static GAOpenMenuTarget* menuTarget;
static NSMenuItem* rootMenuItem;
static NSPanel* progressPanel;
static NSTextField* progressLabel;
static NSTextField* progressDetail;
static NSProgressIndicator* progressBar;
static std::string progressPhase;
static std::chrono::steady_clock::time_point progressUpdated;

namespace gaDelivery {
constexpr unsigned long long kMaxCaptureDisplayBytes = 2ULL * 1024 * 1024 * 1024;
constexpr NSUInteger kMaximumCaptureFiles = 4096;
constexpr NSUInteger kCaptureReceiptFiles = 3;
constexpr std::size_t kMaximumTransferFactsBytes = 1024 * 1024;
constexpr NSUInteger kMaximumNativeTransferFiles =
    kMaximumCaptureFiles + kCaptureReceiptFiles;
// Текстовые факты выводит штатный NSAlert, который сам рассчитывает их размер.
static NSString* sourceFacts(const std::vector<SourceNotice>& facts) {
    NSMutableArray<NSString*>* rows = [NSMutableArray new];
    for (const auto& fact : facts) {
        [rows addObject:[NSString stringWithFormat:@"%@: %@", text(fact.label), text(fact.value)]];
    }
    return [rows componentsJoinedByString:@"\n"];
}
void installMenu(void (*command)()) {
    if (!NSApp.mainMenu) return;
    if (rootMenuItem && rootMenuItem.menu == NSApp.mainMenu) return;
    // AutoCAD rebuilds/replaces its menu when the first document opens. An
    // allocated item is not evidence it is still attached to the visible menu.
    [rootMenuItem.menu removeItem:rootMenuItem];
    openCommand = command;
    menuTarget = [GAOpenMenuTarget new];
    rootMenuItem = [[NSMenuItem alloc] initWithTitle:@"Green Atlas" action:nil keyEquivalent:@""];
    rootMenuItem.submenu = [[NSMenu alloc] initWithTitle:@"Green Atlas"];
    NSMenuItem* item = [[NSMenuItem alloc] initWithTitle:@"Открыть в Green Atlas…" action:@selector(open:) keyEquivalent:@""];
    item.target = menuTarget;
    [rootMenuItem.submenu addItem:item];
    [NSApp.mainMenu addItem:rootMenuItem];
}
void removeMenu() {
    [rootMenuItem.menu removeItem:rootMenuItem];
    rootMenuItem = nil; menuTarget = nil; openCommand = nullptr;
    endStage();
}
bool confirmPreparation(bool modified) {
    NSAlert* alert = [NSAlert new];
    alert.messageText = @"Открыть в Green Atlas";
    alert.informativeText = sourceFacts({
        {"Источник", modified ? "С несохранёнными изменениями" : "Открытый чертёж"},
        {"Подосновы", "Загруженные в AutoCAD"},
        {"Исходный файл", "Без изменений"},
        {"Состояние AutoCAD", "После создания копии чертёж может отмечаться изменённым"},
    });
    [alert addButtonWithTitle:@"Открыть"];
    [alert addButtonWithTitle:@"Отмена"];
    return [alert runModal] == NSAlertFirstButtonReturn;
}
bool confirmLivePartial(const std::vector<SourceNotice>& issues) {
    NSAlert* alert = [NSAlert new];
    alert.alertStyle = NSAlertStyleWarning;
    alert.messageText = @"Есть замечания к исходнику";
    alert.informativeText = sourceFacts(issues);
    [alert addButtonWithTitle:@"Открыть доступные данные"];
    [alert addButtonWithTitle:@"Вернуться в AutoCAD"];
    return [alert runModal] == NSAlertFirstButtonReturn;
}
bool confirmPartial(const std::vector<std::string>& issues) {
    NSAlert* alert = [NSAlert new];
    alert.messageText = @"Не все данные удалось подготовить";
    alert.informativeText = @"Можно продолжить с доступной геометрией или вернуться в AutoCAD. Эти замечания сохранятся вместе с копией.";
    NSScrollView* scroll = [[NSScrollView alloc] initWithFrame:NSMakeRect(0, 0, 430, 120)];
    scroll.hasVerticalScroller = YES;
    NSTextView* details = [[NSTextView alloc] initWithFrame:NSMakeRect(0, 0, 410, 120)];
    details.editable = NO; details.font = [NSFont systemFontOfSize:12];
    NSMutableArray* lines = [NSMutableArray new];
    for (const auto& issue : issues) [lines addObject:text(issue)];
    details.string = [lines componentsJoinedByString:@"\n\n"];
    scroll.documentView = details; alert.accessoryView = scroll;
    [alert addButtonWithTitle:@"Продолжить"];
    [alert addButtonWithTitle:@"Вернуться в AutoCAD"];
    return [alert runModal] == NSAlertFirstButtonReturn;
}
ReferenceRecovery confirmReferenceRecovery(size_t lost, size_t available) {
    NSAlert* alert = [NSAlert new];
    alert.messageText = @"Часть подоснов не попала в копию";
    alert.informativeText = [NSString stringWithFormat:
        @"Ссылок: %lu. Доступны для восстановления: %lu.\nВосстановим доступные ссылки только в копии, остальные будут пропущены. Исходный чертёж не меняется.",
        (unsigned long)lost, (unsigned long)available];
    if (available) [alert addButtonWithTitle:@"Восстановить доступные"];
    [alert addButtonWithTitle:@"Продолжить без них"];
    [alert addButtonWithTitle:@"Вернуться в AutoCAD"];
    const NSModalResponse response = [alert runModal];
    if (available && response == NSAlertFirstButtonReturn) return ReferenceRecovery::RestoreAvailable;
    if (response == (available ? NSAlertSecondButtonReturn : NSAlertFirstButtonReturn)) return ReferenceRecovery::Skip;
    return ReferenceRecovery::Cancel;
}
void error(const std::string& message) {
    NSAlert* alert = [NSAlert new];
    alert.messageText = @"Передача не начата";
    alert.informativeText = text(message);
    [alert addButtonWithTitle:@"Понятно"];
    [alert runModal];
}
void stage(const std::string& message) {
    if (!progressPanel) {
        progressPanel = [[NSPanel alloc] initWithContentRect:NSMakeRect(0, 0, 520, 124)
            styleMask:NSWindowStyleMaskTitled backing:NSBackingStoreBuffered defer:NO];
        progressPanel.title = @"Green Atlas";
        progressLabel = [NSTextField wrappingLabelWithString:@""];
        progressLabel.font = [NSFont systemFontOfSize:15 weight:NSFontWeightSemibold];
        progressLabel.frame = NSMakeRect(24, 72, 472, 28);
        [progressPanel.contentView addSubview:progressLabel];
        progressDetail = [NSTextField labelWithString:@""];
        progressDetail.textColor = NSColor.secondaryLabelColor;
        progressDetail.frame = NSMakeRect(24, 28, 472, 20);
        [progressPanel.contentView addSubview:progressDetail];
        progressBar = [[NSProgressIndicator alloc] initWithFrame:NSMakeRect(24, 57, 472, 6)];
        progressBar.style = NSProgressIndicatorStyleBar;
        progressBar.indeterminate = YES;
        progressBar.usesThreadedAnimation = YES;
        [progressPanel.contentView addSubview:progressBar];
        [progressBar startAnimation:nil];
        [progressPanel center]; [progressPanel orderFront:nil];
    }
    progressLabel.stringValue = text(message);
    progressDetail.stringValue = @"";
    // Прямой показ не обрабатывает события и не вызывает повторный вход в CAD.
    [progressPanel.contentView displayIfNeeded];
    [progressPanel display];
    [CATransaction flush];
}
void progress(const std::string& phase, std::size_t processed) {
    const auto now = std::chrono::steady_clock::now();
    if (phase == progressPhase && now - progressUpdated < std::chrono::milliseconds(100)) return;
    progressPhase = phase;
    progressUpdated = now;
    const char* label = phase == "collecting" ? "Читаем объекты"
        : phase == "inventory" ? "Получаем свойства объектов"
        : phase == "serializing_coverage" ? "Сохраняем перечень объектов"
        : phase == "serializing_paths" ? "Сохраняем геометрию отображения"
        : phase == "archive_instances" ? "Сохраняем свойства экземпляров"
        : phase == "archive_copy" ? "Создаём копии чертежа и подоснов"
        : phase == "archive_files" ? "Сохраняем чертёж и подосновы"
        : "Сохраняем снимок чертежа";
    stage(label);
    if (processed) progressDetail.stringValue = [NSString stringWithFormat:@"Обработано: %lu",
        (unsigned long)processed];
    [progressPanel.contentView displayIfNeeded];
    [progressPanel display];
    [CATransaction flush];
}
void endStage() {
    [progressBar stopAnimation:nil];
    [progressPanel orderOut:nil];
    progressPanel = nil; progressLabel = nil; progressDetail = nil; progressBar = nil;
    progressPhase.clear();
}

std::string createStaging() {
    NSFileManager* files = NSFileManager.defaultManager;
    NSURL* support = [files URLForDirectory:NSApplicationSupportDirectory inDomain:NSUserDomainMask
                         appropriateForURL:nil create:YES error:nil];
    NSURL* root = [support URLByAppendingPathComponent:@"Green Atlas/Transfers" isDirectory:YES];
    if (!root) return {};
    if (![files createDirectoryAtURL:root withIntermediateDirectories:YES
                         attributes:@{NSFilePosixPermissions:@0700} error:nil]) return {};
    if (!GALocalDirectPath(root)) return {};
    NSURL* directory = [root URLByAppendingPathComponent:NSUUID.UUID.UUIDString isDirectory:YES];
    if (![files createDirectoryAtURL:directory withIntermediateDirectories:NO
                         attributes:@{NSFilePosixPermissions:@0700} error:nil]) return {};
    return directory.path.UTF8String;
}

static NSDictionary* fileEntry(NSURL* url, NSString* kind) {
    NSDictionary* attributes = [NSFileManager.defaultManager attributesOfItemAtPath:url.path error:nil];
    if (![attributes[NSFileType] isEqual:NSFileTypeRegular]
        || !GALocalDirectPath(url)) return nil;
    NSInputStream* input = [NSInputStream inputStreamWithURL:url];
    [input open];
    CC_SHA256_CTX context; CC_SHA256_Init(&context);
    uint8_t bytes[65536]; NSInteger count; unsigned long long total = 0;
    while ((count = [input read:bytes maxLength:sizeof(bytes)]) > 0) {
        CC_SHA256_Update(&context, bytes, (CC_LONG)count); total += count;
    }
    [input close];
    if (count < 0 || total != [attributes[NSFileSize] unsignedLongLongValue] || total == 0) return nil;
    unsigned char digest[CC_SHA256_DIGEST_LENGTH]; CC_SHA256_Final(digest, &context);
    NSMutableString* hash = [NSMutableString new];
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; ++i) [hash appendFormat:@"%02x", digest[i]];
    return @{@"name":url.lastPathComponent, @"kind":kind, @"bytes":@(total), @"sha256":hash};
}

static NSDictionary* liveCaptureFileEntry(NSURL* url) {
    NSDictionary* attributes = [NSFileManager.defaultManager attributesOfItemAtPath:url.path error:nil];
    if ([attributes[NSFileSize] unsignedLongLongValue] > kMaxCaptureDisplayBytes) return nil;
    return fileEntry(url, @"live_capture");
}

static NSMutableDictionary* readProbe(NSURL* probe) {
    NSDictionary* attributes = [NSFileManager.defaultManager attributesOfItemAtPath:probe.path error:nil];
    if (![attributes[NSFileType] isEqual:NSFileTypeRegular]
        || [attributes[NSFileSize] unsignedLongLongValue] > kMaxCaptureDisplayBytes
        || !GALocalDirectPath(probe)) return nil;
    NSData* raw = [NSData dataWithContentsOfURL:probe options:NSDataReadingMappedIfSafe error:nil];
    id object = raw ? [NSJSONSerialization JSONObjectWithData:raw options:NSJSONReadingMutableContainers error:nil] : nil;
    return [object isKindOfClass:NSMutableDictionary.class] ? object : nil;
}

// Сводка приходит только из текущего нативного захвата и связана с полным SHA.
static NSDictionary* liveProbe(NSURL* capture, const std::string& facts,
                               NSDictionary* captureEntry = nil) {
    if (facts.empty()) return readProbe(capture);
    if (facts.size() > kMaximumTransferFactsBytes) return nil;
    NSData* data = [text(facts) dataUsingEncoding:NSUTF8StringEncoding];
    id envelope = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
    if (![envelope isKindOfClass:NSDictionary.class]
        || ![envelope[@"probe"] isKindOfClass:NSDictionary.class]) return nil;
    NSDictionary* entry = captureEntry ?: liveCaptureFileEntry(capture);
    if (!entry || [entry[@"bytes"] unsignedLongLongValue] > kMaxCaptureDisplayBytes
        || ![entry[@"sha256"] isEqual:envelope[@"snapshot_sha256"]]) return nil;
    return envelope[@"probe"];
}

std::vector<std::string> probeIssues(const std::string& directory) {
    NSURL* root = [NSURL fileURLWithPath:text(directory) isDirectory:YES];
    NSDictionary* probe = readProbe([root URLByAppendingPathComponent:@"Drawing.dxf.green-atlas.geometry.json"]);
    if (!probe) return {"Файл геометрии не прошёл проверку чтения."};
    NSDictionary* summary = probe[@"summary"];
    if (![summary isKindOfClass:NSDictionary.class]) return {"AutoCAD не предоставил итог проверки геометрии."};
    std::vector<std::string> issues;
    if ([summary[@"cyclic_block_references"] isKindOfClass:NSNumber.class]
        && [summary[@"cyclic_block_references"] unsignedLongLongValue] > 0)
        issues.push_back("В копии обнаружены циклические ссылки. Их содержимое не подготовлено для расчёта.");
    if ([summary[@"unexpanded_minsert_blocks"] isKindOfClass:NSNumber.class]
        && [summary[@"unexpanded_minsert_blocks"] unsignedLongLongValue] > 0)
        issues.push_back("Часть массивов объектов не удалось раскрыть в копии.");
    for (NSString* key in @[@"unresolved_instances", @"unreadable_entities", @"unreadable_block_records"])
        if ([summary[key] isKindOfClass:NSNumber.class] && [summary[key] unsignedLongLongValue] > 0) {
            issues.push_back("Часть объектов недоступна для расчёта. Сервис покажет подробности при открытии.");
            break;
        }
    return issues;
}

std::vector<SourceNotice> liveProbeIssues(const std::string& directory,
                                        const std::string& transferFacts) {
    NSURL* root = [NSURL fileURLWithPath:text(directory) isDirectory:YES];
    NSDictionary* probe = liveProbe([root URLByAppendingPathComponent:@"Drawing.autocad.json"], transferFacts);
    if (!probe) return {{"Чтение снимка", "Ошибка"}};
    NSDictionary* summary = probe[@"summary"];
    NSDictionary* source = probe[@"source"];
    if (![summary isKindOfClass:NSDictionary.class] || ![source isKindOfClass:NSDictionary.class])
        return {{"Проверка снимка", "Нет результата"}};
    std::vector<SourceNotice> issues;
    auto countNotice = [&](NSString* key, const char* label) {
        NSNumber* value = summary[key];
        if ([value isKindOfClass:NSNumber.class] && value.longLongValue > 0)
            issues.push_back({label, std::to_string(value.unsignedLongLongValue)});
    };
    // This is display-extraction coverage, not native calculation coverage.
    // Unsaved state was already shown before capture and is not a defect.
    countNotice(@"unresolved_instances", "Без геометрии отображения");
    countNotice(@"cyclic_block_references", "Циклические ссылки");
    countNotice(@"unexpanded_minsert_blocks", "Нераскрытые массивы");
    size_t missingReferences = 0;
    for (NSDictionary* dependency in probe[@"xref_dependencies"] ?: @[]) {
        if (![dependency isKindOfClass:NSDictionary.class]) continue;
        if ([dependency[@"status"] isEqual:@"unresolved"]) {
            ++missingReferences;
        }
    }
    if (missingReferences) issues.push_back({"Недоступные подосновы", std::to_string(missingReferences)});
    return issues;
}

std::string writeLiveTicket(const std::string& directory, const std::string& version,
                            const std::string& autocadVersion, const std::string& liveSession,
                            const std::string& transferFacts) {
    NSURL* root = [NSURL fileURLWithPath:text(directory) isDirectory:YES];
    NSURL* capture = [root URLByAppendingPathComponent:@"Drawing.autocad.json"];
    NSURL* destination = [root URLByAppendingPathComponent:@"transfer.gatransfer"];
    if ([NSFileManager.defaultManager fileExistsAtPath:destination.path]) return {};
    NSDictionary* captureEntry = liveCaptureFileEntry(capture);
    if (!captureEntry || [captureEntry[@"bytes"] unsignedLongLongValue] > kMaxCaptureDisplayBytes)
        return {};
    NSDictionary* probe = liveProbe(capture, transferFacts, captureEntry);
    NSDictionary* source = probe[@"source"];
    NSString* original = source[@"path"];
    if (![probe[@"capture_mode"] isEqual:@"live_document"]
        || ![probe[@"plugin_version"] isEqual:text(version)]
        || ![original isKindOfClass:NSString.class] || !original.length
        || ![source[@"sha256"] isKindOfClass:NSString.class]) return {};
    // The path is context, not the live geometry source, but a stale disk hash
    // would make its provenance misleading. Never silently substitute disk data.
    NSDictionary* diskEntry = fileEntry([NSURL fileURLWithPath:original], @"source_context");
    if (!diskEntry || ![diskEntry[@"sha256"] isEqual:source[@"sha256"]]) return {};
    NSString* sourceName = original.lastPathComponent;
    if (!sourceName.length || sourceName.length > 240) return {};
#if defined(__arm64__)
    NSString* target = @"macos-arm64";
#else
    NSString* target = @"macos-x86_64";
#endif
    NSMutableDictionary* ticket = [@{@"schema":@"green-atlas.transfer/2", @"plugin_version":text(version),
        @"producer":@{@"autocad_version":text(autocadVersion), @"target":target},
        @"manifest":@{@"entry":@"Drawing.autocad.json", @"files":@[captureEntry]},
        @"source_name":sourceName} mutableCopy];
    if (!liveSession.empty()) {
        NSData* sessionData = [text(liveSession) dataUsingEncoding:NSUTF8StringEncoding];
        NSDictionary* session = [NSJSONSerialization JSONObjectWithData:sessionData options:0 error:nil];
        if (![session isKindOfClass:NSDictionary.class]
            || ![session[@"snapshot_sha256"] isEqual:captureEntry[@"sha256"]]) return {};
        ticket[@"schema"] = @"green-atlas.transfer/4";
        ticket[@"live_session"] = session;
        if (version == "0.1.46" || version == "0.1.47" || version == "0.1.48") {
            NSURL* native = [root URLByAppendingPathComponent:@"Native" isDirectory:YES];
            NSMutableArray* files = [NSMutableArray new];
            NSArray* names = [NSFileManager.defaultManager contentsOfDirectoryAtPath:native.path error:nil];
            if (names.count < 3 || names.count > kMaximumNativeTransferFiles) return {};
            for (NSString* name in names) {
                NSDictionary* entry = fileEntry([native URLByAppendingPathComponent:name], @"native_capture");
                if (!entry) return {};
                NSMutableDictionary* row = [entry mutableCopy];
                [row removeObjectForKey:@"kind"];
                row[@"name"] = [@"Native/" stringByAppendingString:name];
                [files addObject:row];
            }
            for (NSString* name in @[@"Drawing.autocad.json", @"Drawing.autocad.json.inventory.json"]) {
                // Один файл отображения связывает оба каталога передачи одним SHA.
                NSDictionary* entry = [name isEqual:@"Drawing.autocad.json"]
                    ? captureEntry : fileEntry([root URLByAppendingPathComponent:name], @"capture_input");
                if (!entry) return {};
                NSMutableDictionary* row = [entry mutableCopy];
                [row removeObjectForKey:@"kind"];
                row[@"name"] = name;
                [files addObject:row];
            }
            ticket[@"schema"] = @"green-atlas.transfer/5";
            ticket[@"native_session"] = @{@"entry":@"Native/session.json", @"files":files};
        }
    }
    NSData* data = [NSJSONSerialization dataWithJSONObject:ticket options:NSJSONWritingPrettyPrinted error:nil];
    if (!data) return {};
    if (![NSFileManager.defaultManager createFileAtPath:destination.path contents:data
                                            attributes:@{NSFilePosixPermissions:@0600}]) return {};
    return destination.path.UTF8String;
}

std::string writeTicket(const std::string& directory, const std::string& version,
                        const std::string& autocadVersion, const std::vector<std::string>& issues) {
    NSURL* root = [NSURL fileURLWithPath:text(directory) isDirectory:YES];
    NSURL* drawing = [root URLByAppendingPathComponent:@"Drawing.dxf"];
    NSURL* probe = [root URLByAppendingPathComponent:@"Drawing.dxf.green-atlas.geometry.json"];
    NSURL* destination = [root URLByAppendingPathComponent:@"transfer.gatransfer"];
    if ([NSFileManager.defaultManager fileExistsAtPath:destination.path]) return {};
    // Preparation diagnostics travel inside the hashed native evidence, not just a local alert.
    NSMutableDictionary* object = readProbe(probe);
    NSDictionary* dxfEntry = fileEntry(drawing, @"drawing");
    if (!object || !dxfEntry || ![object[@"source"] isKindOfClass:NSDictionary.class]
        || ![object[@"source"][@"sha256"] isEqual:dxfEntry[@"sha256"]]
        || ![object[@"plugin_version"] isEqual:text(version)]) return {};
    NSMutableArray* notices = [NSMutableArray new];
    for (const auto& issue : issues) [notices addObject:text(issue)];
    object[@"preparation"] = @{@"mode":@"autocad-copy-bind", @"notices":notices};
    NSData* prepared = [NSJSONSerialization dataWithJSONObject:object options:0 error:nil];
    if (!prepared || ![prepared writeToURL:probe options:NSDataWritingAtomic error:nil]) return {};
    NSDictionary* probeEntry = fileEntry(probe, @"native_probe");
    if (!dxfEntry || !probeEntry) return {};
    if ([dxfEntry[@"bytes"] unsignedLongLongValue] + [probeEntry[@"bytes"] unsignedLongLongValue] > 2ULL*1024*1024*1024) return {};
#if defined(__arm64__)
    NSString* target = @"macos-arm64";
#else
    NSString* target = @"macos-x86_64";
#endif
    NSDictionary* ticket = @{@"schema":@"green-atlas.transfer/1", @"plugin_version":text(version),
        @"producer":@{@"autocad_version":text(autocadVersion), @"target":target},
        @"manifest":@{@"entry":@"Drawing.dxf", @"files":@[dxfEntry, probeEntry]}};
    NSData* data = [NSJSONSerialization dataWithJSONObject:ticket options:NSJSONWritingPrettyPrinted error:nil];
    if (!data) return {};
    if (![NSFileManager.defaultManager createFileAtPath:destination.path contents:data
                                            attributes:@{NSFilePosixPermissions:@0600}]) return {};
    return destination.path.UTF8String;
}

bool launchConnector(const std::string& ticket, std::string* detail) {
    (void)detail;
    Dl_info image{};
    if (!dladdr((void*)&launchConnector, &image) || !image.dli_fname) return false;
    // ...package/Contents/MacOS/GreenAtlasBridge.bundle/Contents/MacOS/binary
    NSURL* directory = [NSURL fileURLWithPath:[NSString stringWithUTF8String:image.dli_fname]];
    for (int i = 0; i < 5; ++i) directory = directory.URLByDeletingLastPathComponent;
    NSURL* app = [directory URLByAppendingPathComponent:@"Applications/Green Atlas.app"];
    if (!GALocalDirectPath(app) || ![[NSBundle bundleWithURL:app].bundleIdentifier isEqual:@"ru.green-atlas.desktop"]) return false;
    NSWorkspaceOpenConfiguration* config = [NSWorkspaceOpenConfiguration configuration];
    config.activates = NO; // The app raises its own window once the project is ready.
    config.createsNewApplicationInstance = NO; // The local app owns the queue and one workspace runtime.
    [NSWorkspace.sharedWorkspace openURLs:@[[NSURL fileURLWithPath:text(ticket)]]
         withApplicationAtURL:app configuration:config completionHandler:^(NSRunningApplication* application, NSError* failure) {
        if (failure) dispatch_async(dispatch_get_main_queue(), ^{ gaDelivery::error("macOS не смогла открыть приложение передачи. Проверьте установку Green Atlas."); });
    }];
    return true;
}
}
