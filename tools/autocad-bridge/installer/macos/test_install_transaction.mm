#import "install-transaction.h"
#include <cassert>
#include <unistd.h>
#include <fcntl.h>
#include <sys/file.h>

static NSString *RejectFinalVersion;
static NSString *CrashEvent;
static int Assertions;
static NSString *TestExecutable;
static NSURL *PendingJournal;
static void require(BOOL value) { ++Assertions; assert(value); }
static NSURL *at(NSURL *root, NSString *name) { return [root URLByAppendingPathComponent:name]; }
static void directory(NSURL *p) { require([NSFileManager.defaultManager createDirectoryAtURL:p withIntermediateDirectories:YES attributes:nil error:nil]); }
static void text(NSURL *p, NSString *value) { require([value writeToURL:p atomically:YES encoding:NSUTF8StringEncoding error:nil]); }
static NSString *version(NSURL *p) { return [NSString stringWithContentsOfURL:at(p,@"version") encoding:NSUTF8StringEncoding error:nil]; }
static BOOL owned(NSURL *p) { return [version(p) hasPrefix:@"owned-"]; }
static BOOL validate(NSURL *p, NSError **error) {
    BOOL final = [p.lastPathComponent isEqual:@"GreenAtlasBridge.bundle"] || [p.lastPathComponent isEqual:@"Green Atlas.app"];
    BOOL staged = [p.URLByDeletingLastPathComponent.lastPathComponent hasPrefix:@".green-atlas-"];
    return owned(p) && !(RejectFinalVersion && final && !staged && [version(p) isEqual:RejectFinalVersion]);
}
static void hook(NSString *event) {
    if ([event isEqual:CrashEvent]) _exit(86);
    if (PendingJournal && [event isEqual:@"before-cleanup-1"]) {
        NSDictionary *journal=[NSDictionary dictionaryWithContentsOfURL:PendingJournal];
        NSDictionary *row=journal[@"items"][1];
        NSURL *previous=[[NSURL fileURLWithPath:row[@"parent"]] URLByAppendingPathComponent:row[@"previous"]];
        text(at(previous,@"version"),@"unknown-user-file"); PendingJournal=nil;
    }
}
static BOOL journalExists(NSURL *parent) { return [NSFileManager.defaultManager fileExistsAtPath:at(parent,@".green-atlas-install-transaction.plist").path]; }
static int payloadDebris(NSURL *parent) {
    int count = 0;
    for (NSString *name in [NSFileManager.defaultManager contentsOfDirectoryAtPath:parent.path error:nil])
        if ([name containsString:@"-previous-"] || [name containsString:@"-incoming-"]) ++count;
    return count;
}
static void fixture(NSURL *root, NSString *value) { directory(root); text(at(root,@"version"),value); }
static std::vector<GAInstallItem> items(NSURL *root, NSString *label) {
    NSURL *p = at(root,[label stringByAppendingString:@"-addins"]), *a = at(root,[label stringByAppendingString:@"-apps"]);
    return {{at(root,@"source-package"),p,@"GreenAtlasBridge.bundle",@".green-atlas",owned,validate,validate},
            {at(root,@"source-launcher"),a,@"Green Atlas.app",@".green-atlas-launcher",owned,validate,validate}};
}
static NSURL *target(const GAInstallItem &item) { return at(item.parent,item.name); }
static void seed(const std::vector<GAInstallItem> &list) {
    for (const auto &item : list) { fixture(target(item),@"owned-old"); }
}
static void assertVersion(const std::vector<GAInstallItem> &list, NSString *value) {
    for (const auto &item : list) require([version(target(item)) isEqual:value]);
}
static void interrupted(const std::vector<GAInstallItem> &list, NSString *event) {
    NSTask *child=[[NSTask alloc] init]; child.executableURL=[NSURL fileURLWithPath:TestExecutable];
    child.arguments=@[@"--crash-child",list[0].parent.URLByDeletingLastPathComponent.path,
        [list[0].parent.lastPathComponent stringByReplacingOccurrencesOfString:@"-addins" withString:@""],event];
    require([child launchAndReturnError:nil]); [child waitUntilExit]; require(child.terminationStatus==86);
    require(journalExists(list[0].parent));
}
int main(int argc, const char *argv[]) {
    @autoreleasepool {
        TestExecutable=[[NSString stringWithUTF8String:argv[0]] stringByStandardizingPath];
        if(argc==5 && strcmp(argv[1],"--crash-child")==0) {
            NSURL *root=[NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[2]]];
            CrashEvent=[NSString stringWithUTF8String:argv[4]]; GAInstallTransactionHook=hook;
            GAInstallManagedItems(items(root,[NSString stringWithUTF8String:argv[3]]),nil); return 87;
        }
        NSURL *root = at([NSURL fileURLWithPath:NSTemporaryDirectory()].URLByResolvingSymlinksInPath,
            [@"ga-installer-transaction-" stringByAppendingString:NSUUID.UUID.UUIDString]);
        directory(root); fixture(at(root,@"source-package"),@"owned-new"); fixture(at(root,@"source-launcher"),@"owned-new");
        // Успешная установка убирает оба прежних payload, сохраняя посторонние файлы.
        auto success=items(root,@"success"); seed(success);
        NSURL *data=at(success[0].parent,@"Workspace"), *unrelated=at(success[1].parent,@"User.app");
        fixture(data,@"manual-plan"); fixture(unrelated,@"user-data");
        require(GAInstallManagedItems(success,nil)); assertVersion(success,@"owned-new");
        require(!journalExists(success[0].parent));
        for(const auto &item:success) require(payloadDebris(item.parent)==0);
        require([version(data) isEqual:@"manual-plan"] && [version(unrelated) isEqual:@"user-data"]);
        // Проверка окончательного положения отличается от проверки staging: оба старых восстанавливаются.
        auto failure=items(root,@"failure"); seed(failure); RejectFinalVersion=@"owned-new";
        require(!GAInstallManagedItems(failure,nil)); RejectFinalVersion=nil; assertVersion(failure,@"owned-old");
        require(!journalExists(failure[0].parent));
        for(const auto &item:failure) require(payloadDebris(item.parent)==0);
        // Сбой после первого rename, но до commit. Recovery выполняется даже с испорченным новым источником.
        auto before=items(root,@"precommit"); seed(before); interrupted(before,@"after-place-0");
        text(at(root,@"source-package/version"),@"broken");
        require(!GAInstallManagedItems(before,nil)); assertVersion(before,@"owned-old");
        require(!journalExists(before[0].parent)); for(const auto &item:before) require(payloadDebris(item.parent)==0);
        text(at(root,@"source-package/version"),@"owned-new");
        // После commit recovery никогда не возвращает старую сборку: лишь повторяет очистку.
        auto after=items(root,@"postcommit"); seed(after); interrupted(after,@"after-commit"); assertVersion(after,@"owned-new");
        text(at(root,@"source-package/version"),@"broken");
        require(!GAInstallManagedItems(after,nil)); assertVersion(after,@"owned-new");
        require(!journalExists(after[0].parent)); for(const auto &item:after) require(payloadDebris(item.parent)==0);
        text(at(root,@"source-package/version"),@"owned-new");
        // Повтор очистки после частичного commit-cleanup не требует уже удалённого previous.
        auto partial=items(root,@"partial-cleanup"); seed(partial); interrupted(partial,@"before-cleanup-0");
        require(payloadDebris(partial[1].parent)==0); text(at(root,@"source-package/version"),@"broken");
        require(!GAInstallManagedItems(partial,nil)); assertVersion(partial,@"owned-new");
        require(!journalExists(partial[0].parent)); for(const auto &item:partial) require(payloadDebris(item.parent)==0);
        text(at(root,@"source-package/version"),@"owned-new");
        // Второй установщик не начинает транзакцию при занятой блокировке.
        auto concurrent=items(root,@"concurrent"); seed(concurrent);
        int lock=open(at(concurrent[1].parent,@".green-atlas-install.lock").fileSystemRepresentation,O_RDWR|O_CREAT,0600);
        require(lock>=0 && flock(lock,LOCK_EX|LOCK_NB)==0); require(!GAInstallManagedItems(concurrent,nil));
        assertVersion(concurrent,@"owned-old"); require(!journalExists(concurrent[0].parent)); close(lock);
        // Ошибка cleanup текущего commit означает установленную новую версию с pending.
        auto pendingNow=items(root,@"pending-now"); seed(pendingNow);
        PendingJournal=at(pendingNow[0].parent,@".green-atlas-install-transaction.plist"); GAInstallTransactionHook=hook;
        BOOL cleanupPending=NO; require(GAInstallManagedItems(pendingNow,nil,&cleanupPending) && cleanupPending);
        GAInstallTransactionHook=nullptr; assertVersion(pendingNow,@"owned-new"); require(journalExists(pendingNow[0].parent));
        // Но старый pending journal не может подтвердить установку другого requested source.
        text(at(root,@"source-package/version"),@"owned-third");
        require(!GAInstallManagedItems(pendingNow,nil,&cleanupPending) && cleanupPending); assertVersion(pendingNow,@"owned-new");
        NSDictionary *pendingRecord=[NSDictionary dictionaryWithContentsOfURL:at(pendingNow[0].parent,@".green-atlas-install-transaction.plist")];
        NSDictionary *pendingRow=pendingRecord[@"items"][1];
        text(at(at(pendingNow[1].parent,pendingRow[@"previous"]),@"version"),@"owned-old");
        require(GAInstallManagedItems(pendingNow,nil,&cleanupPending) && !cleanupPending);
        require([version(target(pendingNow[0])) isEqual:@"owned-third"]);
        require(!journalExists(pendingNow[0].parent)); for(const auto &item:pendingNow) require(payloadDebris(item.parent)==0);
        text(at(root,@"source-package/version"),@"owned-new");
        // Симлинк вместо parent или целевого app никогда не перезаписывает чужой каталог.
        NSURL *external=at(root,@"external"), *linked=at(root,@"linked-parent"); fixture(external,@"user-original");
        require([NSFileManager.defaultManager createSymbolicLinkAtURL:linked withDestinationURL:external error:nil]);
        auto links=items(root,@"links"); links[0].parent=linked;
        require(!GAInstallManagedItems(links,nil)); require([version(external) isEqual:@"user-original"]);
        auto targetLink=items(root,@"target-link"); directory(targetLink[0].parent);
        require([NSFileManager.defaultManager createSymbolicLinkAtURL:target(targetLink[0]) withDestinationURL:external error:nil]);
        require(!GAInstallManagedItems(targetLink,nil)); require([version(external) isEqual:@"user-original"]);
        // Сохранённый journal не разрешает путь previous за пределами программного parent.
        auto tampered=items(root,@"tampered"); seed(tampered); interrupted(tampered,@"after-place-0");
        NSURL *journal=at(tampered[0].parent,@".green-atlas-install-transaction.plist");
        NSMutableDictionary *record=[[NSDictionary dictionaryWithContentsOfURL:journal] mutableCopy];
        NSMutableArray *rows=[record[@"items"] mutableCopy]; NSMutableDictionary *row=[rows[0] mutableCopy];
        row[@"previous"]=external.path; rows[0]=row; record[@"items"]=rows; require([record writeToURL:journal error:nil]);
        require(!GAInstallManagedItems(tampered,nil)); require([version(external) isEqual:@"user-original"]); require(journalExists(tampered[0].parent));
        // Симлинк вместо exact previous после commit блокирует cleanup без перехода к его содержимому.
        auto late=items(root,@"late-link"); seed(late); interrupted(late,@"after-commit");
        NSDictionary *lateRecord=[NSDictionary dictionaryWithContentsOfURL:at(late[0].parent,@".green-atlas-install-transaction.plist")];
        NSDictionary *lateRow=lateRecord[@"items"][1]; NSURL *previous=at(late[1].parent,lateRow[@"previous"]);
        require([NSFileManager.defaultManager removeItemAtURL:previous error:nil]);
        require([NSFileManager.defaultManager createSymbolicLinkAtURL:previous withDestinationURL:external error:nil]);
        BOOL pending=NO; require(!GAInstallManagedItems(late,nil,&pending) && pending); assertVersion(late,@"owned-new"); require([version(external) isEqual:@"user-original"]);
        require([NSFileManager.defaultManager removeItemAtURL:root error:nil]);
        printf("{\"status\":\"PASS\",\"assertions\":%d,\"scenarios\":11,\"real_bundle_or_profile_modified\":false}\n",Assertions);
    }
}
