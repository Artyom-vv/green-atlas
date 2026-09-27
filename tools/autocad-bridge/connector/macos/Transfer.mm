#import "Transfer.h"
#import "LocalPath.h"
#import <CommonCrypto/CommonDigest.h>
#import <Security/Security.h>

static void fail(NSString *message) {
    @throw [NSException exceptionWithName:@"GreenAtlasTransfer" reason:message userInfo:nil];
}
static NSDictionary *readJSON(NSURL *url) {
    NSDictionary *attributes = [NSFileManager.defaultManager attributesOfItemAtPath:url.path error:nil];
    if (![attributes[NSFileType] isEqual:NSFileTypeRegular] || [attributes[NSFileSize] unsignedLongLongValue] > 1024 * 1024)
        fail(@"Файл передачи имеет недопустимый размер или тип.");
    NSData *data = [NSData dataWithContentsOfURL:url options:0 error:nil];
    id value = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
    if (![value isKindOfClass:NSDictionary.class]) fail(@"Файл передачи повреждён. Подготовьте чертёж заново.");
    return value;
}
static NSString *hexDigest(const unsigned char *bytes) {
    NSMutableString *result = [NSMutableString string];
    for (NSUInteger i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) [result appendFormat:@"%02x", bytes[i]];
    return result;
}
static NSString *dataHash(NSData *data) {
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data.bytes, (CC_LONG)data.length, digest);
    return hexDigest(digest);
}
static NSString *fileHash(NSURL *url, void (^check)(void)) {
    NSInputStream *input = [NSInputStream inputStreamWithURL:url];
    [input open];
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    unsigned char buffer[65536];
    NSInteger count;
    @try {
        while ((count = [input read:buffer maxLength:sizeof(buffer)]) > 0) {
            if (check) check();
            CC_SHA256_Update(&context, buffer, (CC_LONG)count);
        }
    } @finally { [input close]; }
    if (count < 0) fail(@"Не удалось прочитать подготовленный файл.");
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(digest, &context);
    return hexDigest(digest);
}

@interface GATransfer ()
@property(strong) NSURL *ticketURL;
@property(strong) NSDictionary *ticket;
@property(strong) NSMutableDictionary *state;
@property(strong) NSURLSession *session;
@property(copy) NSString *origin;
@property(atomic) BOOL cancelConfirmed;
@end

@implementation GATransfer
+ (BOOL)validOrigin:(NSString *)origin {
    NSURLComponents *url = [NSURLComponents componentsWithString:origin];
    BOOL local = [@[@"localhost", @"127.0.0.1", @"[::1]", @"::1"] containsObject:url.host];
    return url.host.length && !url.user.length && !url.password.length
        && !url.query.length && !url.fragment.length && !url.path.length
        && [origin rangeOfCharacterFromSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].location == NSNotFound
        && (!url.port || (url.port.integerValue > 0 && url.port.integerValue <= 65535))
        && ([url.scheme isEqual:@"https"] || ([url.scheme isEqual:@"http"] && local));
}
- (instancetype)initWithTicket:(NSURL *)ticket {
    if ((self = [super init])) {
        _ticketURL = ticket; // Keep the POSIX path; Foundation may abbreviate /private/var.
        if (!GALocalDirectPath(_ticketURL))
            fail(@"Передача не должна находиться за символической ссылкой.");
        _ticket = readJSON(ticket);
        if (![_ticket[@"schema"] isEqual:@"green-atlas.transfer/1"])
            fail(@"Неизвестная версия передачи. Обновите плагин.");
        NSURLSessionConfiguration *config = NSURLSessionConfiguration.ephemeralSessionConfiguration;
        config.timeoutIntervalForRequest = 60;
        config.timeoutIntervalForResource = 90;
        config.HTTPShouldSetCookies = NO;
        config.URLCredentialStorage = nil;
        _session = [NSURLSession sessionWithConfiguration:config delegate:self delegateQueue:nil];
    }
    return self;
}
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task
 willPerformHTTPRedirection:(NSHTTPURLResponse *)response newRequest:(NSURLRequest *)request
 completionHandler:(void (^)(NSURLRequest *))completionHandler {
    completionHandler(nil); // Never redirect a file or device secret, even to another HTTPS host.
}
- (NSURL *)stateURL { return [self.ticketURL URLByAppendingPathExtension:@"state.json"]; }
- (void)save {
    NSData *data = [NSJSONSerialization dataWithJSONObject:self.state options:0 error:nil];
    NSURL *temporary = [self.stateURL URLByAppendingPathExtension:NSUUID.UUID.UUIDString];
    if (!data || ![NSFileManager.defaultManager createFileAtPath:temporary.path contents:data attributes:@{NSFilePosixPermissions:@0600}])
        fail(@"Не удалось сохранить состояние передачи.");
    if (rename(temporary.fileSystemRepresentation, self.stateURL.fileSystemRepresentation) != 0) {
        [NSFileManager.defaultManager removeItemAtURL:temporary error:nil];
        fail(@"Не удалось сохранить ключ передачи.");
    }
}
- (void)check {
    if (self.cancelled) fail(self.cancelConfirmed ? @"Отправка отменена." : @"Отправка остановлена. Отмена на сервере ещё не подтверждена.");
}
- (NSDictionary *)request:(NSString *)method path:(NSString *)path body:(NSData *)body
                    json:(BOOL)json digest:(NSString *)digest allowMissing:(BOOL)allowMissing {
    [self check];
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:[self.origin stringByAppendingString:path]]];
    request.HTTPMethod = method;
    request.HTTPBody = body;
    if (body) [request setValue:json ? @"application/json" : @"application/octet-stream" forHTTPHeaderField:@"Content-Type"];
    if (self.state[@"secret"]) [request setValue:self.state[@"secret"] forHTTPHeaderField:@"X-Green-Atlas-Transfer-Token"];
    if (digest) [request setValue:digest forHTTPHeaderField:@"X-Chunk-SHA256"];
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    __block NSData *received;
    __block NSError *error;
    __block NSInteger status;
    self.activeTask = [self.session dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *problem) {
        received = data; error = problem; status = [(NSHTTPURLResponse *)response statusCode];
        dispatch_semaphore_signal(done);
    }];
    [self.activeTask resume];
    dispatch_semaphore_wait(done, DISPATCH_TIME_FOREVER);
    self.activeTask = nil;
    [self check];
    if (error) fail(@"Нет соединения с сервисом. Принятые части сохранены — повторите передачу.");
    if (allowMissing && (status == 404 || status == 409 || (status == 403 && [path hasSuffix:@"/upload"]))) return nil;
    id payload = received ? [NSJSONSerialization JSONObjectWithData:received options:0 error:nil] : nil;
    if (status < 200 || status >= 300) {
        NSString *message = [payload isKindOfClass:NSDictionary.class] ? payload[@"message"] : nil;
        fail([message isKindOfClass:NSString.class] ? message : @"Сервис не принял запрос. Проверьте адрес и повторите.");
    }
    if (![payload isKindOfClass:NSDictionary.class]) fail(@"Сервис вернул непонятный ответ.");
    return payload;
}
- (NSDictionary *)jsonRequest:(NSString *)method path:(NSString *)path value:(NSDictionary *)value missing:(BOOL)missing {
    return [self request:method path:path body:value ? [NSJSONSerialization dataWithJSONObject:value options:0 error:nil] : nil json:YES digest:nil allowMissing:missing];
}
- (NSURL *)localFile:(NSString *)name {
    if (![name isKindOfClass:NSString.class] || ![name.lastPathComponent isEqual:name]
        || [name containsString:@"\\"] || [name containsString:@":"] || [name hasPrefix:@"."])
        fail(@"Недопустимое имя подготовленного файла.");
    NSURL *url = [self.ticketURL.URLByDeletingLastPathComponent URLByAppendingPathComponent:name];
    if (!GALocalDirectPath(url)) fail(@"Передаваемый файл оказался ссылкой.");
    return url;
}
- (void)stage:(NSString *)stage code:(NSString *)code {
    if (self.progress) self.progress(stage, code ?: @"");
}
- (void)pause:(NSTimeInterval)seconds {
    for (NSTimeInterval elapsed = 0; elapsed < seconds; elapsed += .2) {
        [self check]; [NSThread sleepForTimeInterval:.2];
    }
}
- (NSURL *)project:(NSDictionary *)receipt {
    if (![receipt[@"status"] isEqual:@"needs_review"]) return nil;
    NSString *project = receipt[@"project_id"];
    if (![[NSUUID alloc] initWithUUIDString:project]) fail(@"Сервис вернул неверный проект.");
    NSString *path = [NSString stringWithFormat:@"/projects/%@/import?source=cad", project];
    if (![receipt[@"project_path"] isEqual:path]) fail(@"Сервис вернул небезопасный адрес проекта.");
    return [NSURL URLWithString:[self.origin stringByAppendingString:path]];
}
- (NSURL *)runToOrigin:(NSString *)origin {
    if (![GATransfer validOrigin:origin]) fail(@"Укажите HTTPS-адрес сервиса без пути. Для локального сервиса разрешён localhost.");
    self.origin = origin;
    [self check];
    [self stage:@"Проверяем подготовленный комплект" code:nil];
    NSDictionary *manifest = self.ticket[@"manifest"];
    NSArray *files = manifest[@"files"];
    if (![files isKindOfClass:NSArray.class] || files.count < 2 || files.count > 128)
        fail(@"В комплекте нет файлов AutoCAD.");
    NSMutableDictionary *byName = [NSMutableDictionary dictionary];
    for (NSDictionary *file in files) {
        [self check];
        NSURL *url = [self localFile:file[@"name"]];
        NSDictionary *attributes = [NSFileManager.defaultManager attributesOfItemAtPath:url.path error:nil];
        if (![attributes[NSFileType] isEqual:NSFileTypeRegular] || ![attributes[NSFileSize] isEqual:file[@"bytes"]]
            || ![fileHash(url, ^{ [self check]; }) isEqual:file[@"sha256"]]) fail(@"Подготовленный файл изменился. Создайте новый снимок в AutoCAD.");
        byName[file[@"name"]] = file;
    }
    if ([NSFileManager.defaultManager fileExistsAtPath:self.stateURL.path]) {
        if (!GALocalDirectPath(self.stateURL)) fail(@"Файл состояния оказался ссылкой.");
        self.state = [readJSON(self.stateURL) mutableCopy];
        if (![self.state[@"origin"] isEqual:origin]) fail(@"У этой передачи другой адрес сервиса. Начните новую передачу в AutoCAD.");
        if (![self.state[@"ticket_sha256"] isEqual:fileHash(self.ticketURL, ^{ [self check]; })]) fail(@"Состав передачи изменился. Подготовьте новый снимок в AutoCAD.");
    } else {
        unsigned char bytes[32];
        if (SecRandomCopyBytes(kSecRandomDefault, sizeof(bytes), bytes) != errSecSuccess) fail(@"Не удалось создать ключ передачи.");
        NSString *secret = [[NSData dataWithBytes:bytes length:sizeof(bytes)] base64EncodedStringWithOptions:0];
        secret = [[[secret stringByReplacingOccurrencesOfString:@"+" withString:@"-"] stringByReplacingOccurrencesOfString:@"/" withString:@"_"] stringByReplacingOccurrencesOfString:@"=" withString:@""];
        self.state = [@{@"origin": origin, @"id": NSUUID.UUID.UUIDString.lowercaseString, @"secret": secret,
                       @"ticket_sha256": fileHash(self.ticketURL, ^{ [self check]; })} mutableCopy];
        [self save];
    }
    NSString *identifier = self.state[@"id"];
    if (![[NSUUID alloc] initWithUUIDString:identifier] || [self.state[@"secret"] length] < 43) fail(@"Состояние передачи повреждено.");
    NSString *base = [@"/api/cad-bridge/device/transfers/" stringByAppendingString:identifier];
    NSDictionary *published = [self jsonRequest:@"GET" path:[base stringByAppendingString:@"/publication"] value:nil missing:YES];
    NSURL *project = [self project:published];
    if (project) return project;
    NSDictionary *upload = [self jsonRequest:@"GET" path:[base stringByAppendingString:@"/upload"] value:nil missing:YES];
    if (!upload) {
        NSDictionary *receipt = [self jsonRequest:@"POST" path:@"/api/cad-bridge/device/transfers" value:@{
            @"request_id": identifier, @"client_secret": self.state[@"secret"],
            @"plugin_version": self.ticket[@"plugin_version"], @"manifest": manifest} missing:NO];
        NSString *expected = [NSString stringWithFormat:@"%@/connect/autocad/%@", origin, identifier];
        if (![receipt[@"verification_url"] isEqual:expected]) fail(@"Сервис вернул небезопасный адрес подтверждения.");
        if ([receipt[@"status"] isEqual:@"awaiting_approval"]) {
            [self stage:@"Подтвердите передачу в браузере" code:receipt[@"confirmation_code"]];
            if (self.openBrowser) self.openBrowser([NSURL URLWithString:expected]);
            while ([receipt[@"status"] isEqual:@"awaiting_approval"]) {
                [self pause:5];
                receipt = [self jsonRequest:@"GET" path:base value:nil missing:NO];
            }
        }
        if (![receipt[@"status"] isEqual:@"approved"]) fail(@"Передача не подтверждена или срок истёк. Начните заново в AutoCAD.");
        upload = [self jsonRequest:@"POST" path:[base stringByAppendingString:@"/upload"] value:nil missing:NO];
    }
    NSUInteger chunkBytes = [upload[@"chunk_bytes"] unsignedIntegerValue];
    if (!chunkBytes || chunkBytes > 4 * 1024 * 1024) fail(@"Сервис вернул неверный размер части файла.");
    if (![upload[@"files"] isKindOfClass:NSArray.class] || [upload[@"files"] count] != files.count) fail(@"Состав передачи на сервере изменился.");
    for (NSDictionary *file in upload[@"files"]) {
        if (![file[@"index"] isKindOfClass:NSNumber.class] || [file[@"index"] integerValue] < 0
            || [file[@"index"] unsignedIntegerValue] >= files.count) fail(@"Сервис вернул неверный номер файла.");
        NSDictionary *local = byName[file[@"name"]];
        if (!local || ![local[@"bytes"] isEqual:file[@"bytes"]]) fail(@"Состав передачи на сервере изменился.");
        unsigned long long offset = [file[@"received_bytes"] unsignedLongLongValue];
        unsigned long long total = [file[@"bytes"] unsignedLongLongValue];
        if (offset > total) fail(@"Сервис вернул неверное состояние файла.");
        NSFileHandle *input = [NSFileHandle fileHandleForReadingFromURL:[self localFile:file[@"name"]] error:nil];
        @try {
            while (offset < total) {
                [self check];
                [self stage:[NSString stringWithFormat:@"Передаём %@", file[@"name"]] code:nil];
                [input seekToFileOffset:offset];
                NSUInteger length = (NSUInteger)MIN((unsigned long long)chunkBytes, total - offset);
                NSData *data = [input readDataOfLength:length];
                if (data.length != length) fail(@"Подготовленный файл не удалось дочитать.");
                NSString *path = [NSString stringWithFormat:@"%@/files/%@?offset=%llu", base, file[@"index"], offset];
                [self request:@"PUT" path:path body:data json:NO digest:dataHash(data) allowMissing:NO];
                offset += length;
            }
        } @finally { [input closeFile]; }
    }
    [self stage:@"Проверяем целостность файлов" code:nil];
    [self jsonRequest:@"POST" path:[base stringByAppendingString:@"/upload/finish"] value:nil missing:NO];
    [self stage:@"Создаём проект" code:nil];
    published = [self jsonRequest:@"POST" path:[base stringByAppendingString:@"/publication"] value:self.ticket[@"producer"] missing:NO];
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:660];
    while ([published[@"status"] isEqual:@"processing"] && deadline.timeIntervalSinceNow > 0) {
        [self pause:3];
        published = [self jsonRequest:@"GET" path:[base stringByAppendingString:@"/publication"] value:nil missing:NO];
    }
    project = [self project:published];
    if (!project) fail(@"Создание проекта не завершено. Повторите — файлы не будут загружены заново.");
    return project;
}
- (void)cancel {
    self.cancelled = YES;
    [self.activeTask cancel];
    // A separate request can revoke consent even while the current upload is cancelled.
    if (!self.origin || !self.state[@"id"]) return;
    NSString *path = [NSString stringWithFormat:@"%@/api/cad-bridge/device/transfers/%@/cancel", self.origin, self.state[@"id"]];
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:path]];
    request.HTTPMethod = @"POST";
    [request setValue:self.state[@"secret"] forHTTPHeaderField:@"X-Green-Atlas-Transfer-Token"];
    [[self.session dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        if (!error && [(NSHTTPURLResponse *)response statusCode] == 200) {
            self.cancelConfirmed = YES;
            [self stage:@"Отправка отменена. Созданный ранее проект сохранён." code:nil];
        }
    }] resume];
}
- (void)close { [self.session finishTasksAndInvalidate]; }
+ (int)selfTest {
    for (NSString *url in @[@"https://green.example", @"http://localhost:5173", @"http://127.0.0.1:15173"])
        if (![self validOrigin:url]) return 1;
    for (NSString *url in @[@"http://external.example", @"https://user:password@green.example", @"https://green.example/path", @"file:///tmp/foo", @"https://green.example?token=secret"])
        if ([self validOrigin:url]) return 2;
    if (![dataHash([@"abc" dataUsingEncoding:NSUTF8StringEncoding]) isEqual:@"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"])
        return 3;
    puts("origin_validation_and_sha256: passed");
    return 0;
}
@end
