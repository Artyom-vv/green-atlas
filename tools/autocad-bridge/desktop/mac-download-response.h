#pragma once

#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

// A forced WKDownload also accepts HTTP error bodies. Reject them before a
// destination is offered, so an API refusal cannot be saved as a CAD artifact.
static inline BOOL GARejectReleaseDownloadResponse(
    NSURLResponse *response,
    void (^completion)(NSURL * _Nullable),
    void (^report)(NSString *)) {
    NSHTTPURLResponse *http = [response isKindOfClass:NSHTTPURLResponse.class]
        ? (NSHTTPURLResponse *)response : nil;
    if (http && http.statusCode >= 200 && http.statusCode < 300) return NO;
    completion(nil);
    NSString *explanation = http
        ? [NSString stringWithFormat:@"Локальный сервис не выдал файл выпуска (HTTP %ld). Файл не сохранён. Проверьте состояние выпуска в проекте.", (long)http.statusCode]
        : @"Локальный сервис вернул неподдерживаемый ответ. Файл выпуска не сохранён.";
    report(explanation);
    return YES;
}

NS_ASSUME_NONNULL_END
