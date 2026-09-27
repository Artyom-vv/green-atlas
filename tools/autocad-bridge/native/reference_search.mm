#import <AppKit/AppKit.h>
#import <CommonCrypto/CommonDigest.h>
#import "../connector/macos/LocalPath.h"
#include "reference_search.h"
#include <map>
#include <stdexcept>
#include <algorithm>

namespace gaDelivery {
namespace {
NSString* ns(const std::string& value) { return [NSString stringWithUTF8String:value.c_str()] ?: @""; }
std::string canonical(NSString* value) {
    return value.precomposedStringWithCanonicalMapping.lowercaseString.UTF8String;
}
std::string hash(NSURL* path) {
    NSInputStream* stream = [NSInputStream inputStreamWithURL:path];
    [stream open];
    CC_SHA256_CTX context; CC_SHA256_Init(&context);
    unsigned char buffer[65536]; NSInteger count;
    while ((count = [stream read:buffer maxLength:sizeof(buffer)]) > 0)
        CC_SHA256_Update(&context, buffer, static_cast<CC_LONG>(count));
    [stream close];
    if (count < 0) return {};
    unsigned char digest[CC_SHA256_DIGEST_LENGTH]; CC_SHA256_Final(digest, &context);
    NSMutableString* result = [NSMutableString new];
    for (const auto byte : digest) [result appendFormat:@"%02x", byte];
    return result.UTF8String;
}
}

std::vector<ReferencePathMatch> findReferencePaths(
    const std::string& rootPath, const std::vector<ReferencePathRequest>& requests) {
    NSURL* root = [NSURL fileURLWithPath:ns(rootPath) isDirectory:YES];
    if (!GALocalDirectPath(root)) throw std::runtime_error("Папка комплекта содержит символическую ссылку.");
    BOOL directory = NO;
    if (![NSFileManager.defaultManager fileExistsAtPath:root.path isDirectory:&directory] || !directory)
        throw std::runtime_error("Папка комплекта недоступна.");
    __block bool unreadable = false;
    NSDirectoryEnumerator* enumerator = [NSFileManager.defaultManager enumeratorAtURL:root
        includingPropertiesForKeys:@[NSURLIsRegularFileKey, NSURLIsSymbolicLinkKey]
        options:NSDirectoryEnumerationSkipsPackageDescendants
        errorHandler:^BOOL(NSURL* url, NSError* error) { unreadable = true; return YES; }];
    std::map<std::string, std::vector<std::string>> byName;
    size_t count = 0;
    for (NSURL* file in enumerator) {
        if (++count > 200000) throw std::runtime_error("Папка слишком большая. Выберите папку нужного чертежа.");
        NSNumber* symlink = nil; [file getResourceValue:&symlink forKey:NSURLIsSymbolicLinkKey error:nil];
        // NSURL enumeration does not descend into symlinks. skipDescendants on
        // a file can skip the following directory and hide valid candidates.
        if (symlink.boolValue) continue;
        NSNumber* regular = nil; [file getResourceValue:&regular forKey:NSURLIsRegularFileKey error:nil];
        NSString* extension = file.pathExtension.lowercaseString;
        if (!regular.boolValue || !([extension isEqual:@"dwg"] || [extension isEqual:@"dxf"])) continue;
        if (!GALocalDirectPath(file)) continue;
        byName[canonical(file.lastPathComponent)].push_back(file.path.UTF8String);
    }
    if (unreadable) throw std::runtime_error("Не все папки доступны для поиска. Выберите доступную папку комплекта.");
    std::vector<ReferencePathMatch> result;
    std::map<std::string, std::string> hashes;
    for (const auto& request : requests) {
        NSString* stored = [ns(request.storedPath) stringByReplacingOccurrencesOfString:@"\\" withString:@"/"];
        ReferencePathMatch match{request.name, {}, "missing", {}};
        match.candidates = byName[canonical(stored.lastPathComponent)];
        std::sort(match.candidates.begin(), match.candidates.end());
        // Prefer an exact authored relative suffix over a basename in another
        // subpackage. Parent traversal is never accepted as an exact suffix.
        while ([stored hasPrefix:@"./"]) stored = [stored substringFromIndex:2];
        bool relative = !stored.isAbsolutePath && ![stored.pathComponents containsObject:@".."]
            && [stored.pathComponents count] > 1 && [stored rangeOfString:@":"].location == NSNotFound;
        std::vector<std::string> suffix;
        for (const auto& candidate : match.candidates)
            if (relative && [ns(canonical(ns(candidate))) hasSuffix:ns("/" + canonical(stored))])
                suffix.push_back(candidate);
        const auto& pool = suffix.empty() ? match.candidates : suffix;
        if (pool.size() == 1) {
            match.path = pool.front();
            match.status = suffix.empty() ? "unique_name" : "exact_suffix";
        } else if (pool.size() > 1) {
            std::string expected;
            bool identical = true;
            for (const auto& candidate : pool) {
                if (!hashes.count(candidate)) hashes[candidate] = hash([NSURL fileURLWithPath:ns(candidate)]);
                const auto& digest = hashes[candidate];
                if (digest.empty() || (!expected.empty() && digest != expected)) identical = false;
                expected = digest;
            }
            match.status = identical ? "identical_copies" : "ambiguous";
            if (identical) match.path = pool.front();
        }
        result.push_back(std::move(match));
    }
    return result;
}

bool chooseReferencePaths(const std::vector<ReferencePathRequest>& requests,
                          std::vector<ReferencePathMatch>& chosen) {
    if (requests.empty()) return true;
    NSAlert* start = [NSAlert new];
    start.messageText = @"Найдём подосновы в комплекте?";
    start.informativeText = [NSString stringWithFormat:
        @"AutoCAD не загрузил %lu ссылок. Выберите папку комплекта — предложим новые пути. Изменения коснутся только копии.", requests.size()];
    [start addButtonWithTitle:@"Выбрать папку…"];
    [start addButtonWithTitle:@"Продолжить без них"];
    [start addButtonWithTitle:@"Отмена"];
    const auto action = [start runModal];
    if (action == NSAlertSecondButtonReturn) return true;
    if (action != NSAlertFirstButtonReturn) return false;
    NSOpenPanel* panel = [NSOpenPanel openPanel];
    panel.canChooseFiles = NO; panel.canChooseDirectories = YES;
    panel.allowsMultipleSelection = NO; panel.prompt = @"Найти подосновы";
    if ([panel runModal] != NSModalResponseOK) return false;
    const auto found = findReferencePaths(panel.URL.path.UTF8String, requests);
    NSMutableArray* lines = [NSMutableArray new];
    size_t available = 0;
    for (const auto& match : found) {
        if (!match.path.empty()) ++available;
        [lines addObject:[NSString stringWithFormat:@"%@\n%@", ns(match.name),
            match.path.empty() ? (match.status == "ambiguous" ? @"Несколько разных файлов — не подставляем" : @"Не найдено") : ns(match.path)]];
    }
    NSAlert* review = [NSAlert new];
    review.messageText = [NSString stringWithFormat:@"Найдено подоснов: %lu из %lu", available, requests.size()];
    review.informativeText = @"Проверьте пути. Совпадающие имена с разным содержимым не заменяются автоматически. Остальные замечания сохранятся.";
    NSScrollView* scroll = [[NSScrollView alloc] initWithFrame:NSMakeRect(0,0,480,180)];
    scroll.hasVerticalScroller = YES;
    NSTextView* details = [[NSTextView alloc] initWithFrame:NSMakeRect(0,0,460,180)];
    details.editable = NO; details.font = [NSFont systemFontOfSize:12];
    details.string = [lines componentsJoinedByString:@"\n\n"];
    scroll.documentView = details; review.accessoryView = scroll;
    [review addButtonWithTitle:available ? @"Применить к копии" : @"Продолжить без них"];
    [review addButtonWithTitle:@"Без замены путей"];
    [review addButtonWithTitle:@"Отмена"];
    const auto decision = [review runModal];
    if (decision == NSAlertThirdButtonReturn) return false;
    if (decision == NSAlertFirstButtonReturn)
        for (const auto& match : found) if (!match.path.empty()) chosen.push_back(match);
    return true;
}
}
