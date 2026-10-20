#import <AppKit/AppKit.h>

// This entry has no runtime or workspace. Each launch resolves the canonical
// installed bundle, so an update/rollback never leaves an alias to a backup.
static NSURL *installedDesktop() {
    NSURL *support = [NSFileManager.defaultManager URLsForDirectory:NSApplicationSupportDirectory inDomains:NSUserDomainMask].firstObject;
    NSString *qa = [NSBundle.mainBundle objectForInfoDictionaryKey:@"GADevelopmentPackageRoot"];
    NSURL *package = qa.length ? [NSURL fileURLWithPath:qa] : [support URLByAppendingPathComponent:@"Autodesk/ApplicationAddins/GreenAtlasBridge.bundle"];
    NSURL *desktop = [package URLByAppendingPathComponent:@"Contents/Applications/Green Atlas.app"];
    if (![desktop.path isEqual:desktop.URLByResolvingSymlinksInPath.path]) return nil;
    NSDictionary *info = [NSDictionary dictionaryWithContentsOfURL:[desktop URLByAppendingPathComponent:@"Contents/Info.plist"]];
    if (![info[@"CFBundleIdentifier"] isEqual:@"ru.green-atlas.desktop"]
        && !(qa.length && [info[@"CFBundleIdentifier"] isEqual:@"ru.green-atlas.desktop.qa"])) return nil;
    if (![NSFileManager.defaultManager isExecutableFileAtPath:[desktop URLByAppendingPathComponent:@"Contents/MacOS/GreenAtlasDesktop"].path]) return nil;
    return desktop;
}
@interface Launcher : NSObject <NSApplicationDelegate>
@end
@implementation Launcher
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    NSURL *desktop = installedDesktop();
    if (!desktop) {
        NSAlert *alert = [[NSAlert alloc] init];
        alert.messageText = @"Green Atlas не установлен";
        alert.informativeText = @"Установите полный пакет Green Atlas, затем откройте приложение снова.";
        [alert runModal]; [NSApp terminate:nil]; return;
    }
    NSWorkspaceOpenConfiguration *configuration = [NSWorkspaceOpenConfiguration configuration];
    configuration.activates = YES;
    [NSWorkspace.sharedWorkspace openApplicationAtURL:desktop configuration:configuration completionHandler:^(NSRunningApplication *app, NSError *error) {
        dispatch_async(dispatch_get_main_queue(), ^{
            if (error) {
                NSAlert *alert = [[NSAlert alloc] init]; alert.messageText = @"Не удалось открыть Green Atlas";
                alert.informativeText = error.localizedDescription; [alert runModal];
            }
            [NSApp terminate:nil];
        });
    }];
}
@end
int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc == 2 && strcmp(argv[1], "--resolve") == 0) {
            NSURL *target = installedDesktop();
            if (!target) return 1;
            printf("%s\n", target.path.UTF8String); return 0;
        }
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
        Launcher *delegate = [[Launcher alloc] init]; NSApp.delegate = delegate; [NSApp run];
    }
}
