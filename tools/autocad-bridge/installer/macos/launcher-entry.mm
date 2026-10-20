#import "launcher-entry.h"
#import "install-transaction.h"

static NSError *entryError(NSString *text) {
    return [NSError errorWithDomain:@"ru.green-atlas.launcher" code:1 userInfo:@{NSLocalizedDescriptionKey:text}];
}
BOOL GAOwnedLauncher(NSURL *entry) {
    NSDictionary *attributes = [NSFileManager.defaultManager attributesOfItemAtPath:entry.path error:nil];
    NSDictionary *info = [NSDictionary dictionaryWithContentsOfURL:[entry URLByAppendingPathComponent:@"Contents/Info.plist"]];
    return [attributes[NSFileType] isEqual:NSFileTypeDirectory]
        && [entry.path isEqual:entry.URLByResolvingSymlinksInPath.path]
        && [info[@"CFBundleIdentifier"] isEqual:@"ru.green-atlas.launcher"];
}
BOOL GAValidLauncher(NSURL *source, NSError **error) {
    NSDictionary *info = [NSDictionary dictionaryWithContentsOfURL:[source URLByAppendingPathComponent:@"Contents/Info.plist"]];
    if (!GAOwnedLauncher(source.URLByResolvingSymlinksInPath) || info[@"GADevelopmentPackageRoot"]
        || ![info[@"CFBundleExecutable"] isEqual:@"GreenAtlasLauncher"]) {
        if (error) *error = entryError(@"Пакет не содержит корректного ярлыка Green Atlas."); return NO;
    }
    for (NSString *relative in @[@"Contents/Info.plist",@"Contents/MacOS/GreenAtlasLauncher"]) {
        NSURL *file = [source URLByAppendingPathComponent:relative];
        NSURL *expected = [source.URLByResolvingSymlinksInPath URLByAppendingPathComponent:relative];
        if (![NSFileManager.defaultManager attributesOfItemAtPath:file.path error:nil][NSFileType]
            || ![[NSFileManager.defaultManager attributesOfItemAtPath:file.path error:nil][NSFileType] isEqual:NSFileTypeRegular]
            || ![file.URLByResolvingSymlinksInPath.path isEqual:expected.path]) {
            if (error) *error = entryError(@"Ярлык содержит недопустимую ссылку или отсутствующий файл."); return NO;
        }
    }
    NSTask *task = [[NSTask alloc] init];
    task.executableURL = [NSURL fileURLWithPath:@"/usr/bin/codesign"];
    task.arguments = @[@"--verify", @"--deep", @"--strict", source.path];
    task.standardOutput = NSFileHandle.fileHandleWithNullDevice;
    task.standardError = NSFileHandle.fileHandleWithNullDevice;
    if (![task launchAndReturnError:error]) return NO;
    [task waitUntilExit];
    if (task.terminationStatus != 0) { if (error) *error = entryError(@"Подпись ярлыка Green Atlas повреждена."); return NO; }
    return YES;
}
BOOL GALauncherPreflight(NSURL *source, NSURL *applications, NSError **error) {
    if (!GAValidLauncher(source, error)) return NO;
    if (![applications.path isEqual:applications.URLByResolvingSymlinksInPath.path]) {
        if (error) *error = entryError(@"Каталог приложений содержит символическую ссылку."); return NO;
    }
    NSURL *entry = [applications URLByAppendingPathComponent:@"Green Atlas.app"];
    if ([NSFileManager.defaultManager attributesOfItemAtPath:entry.path error:nil] && !GAOwnedLauncher(entry)) {
        if (error) *error = entryError(@"В Applications уже находится постороннее приложение Green Atlas. Оно не изменено."); return NO;
    }
    return YES;
}
BOOL GAInstallLauncher(NSURL *source, NSURL *applications, NSURL **backup, NSError **error) {
    if (backup) *backup = nil;
    return GAInstallManagedItems({{source, applications, @"Green Atlas.app", @".green-atlas-launcher", GAOwnedLauncher, GAValidLauncher, GAValidLauncher}}, error);
}
