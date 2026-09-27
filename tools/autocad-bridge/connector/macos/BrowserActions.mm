#import "BrowserActions.h"

@interface GABrowserActions ()
@property(strong) NSURL *url;
@property(strong) NSButton *openButton;
@property(strong) NSButton *linkCopyButton;
@end

@implementation GABrowserActions
- (instancetype)initWithFrame:(NSRect)frame {
    if ((self = [super initWithFrame:frame])) {
        _openButton = [NSButton buttonWithTitle:@"Открыть браузер" target:self action:@selector(openClicked:)];
        _openButton.frame = NSMakeRect(0, 0, 182, 34);
        [self addSubview:_openButton];
        _linkCopyButton = [NSButton buttonWithTitle:@"Скопировать ссылку" target:self action:@selector(copyClicked:)];
        _linkCopyButton.frame = NSMakeRect(194, 0, 208, 34);
        [self addSubview:_linkCopyButton];
        [self clear];
    }
    return self;
}
- (void)presentURL:(NSURL *)url project:(BOOL)project {
    self.url = url;
    self.openButton.title = project ? @"Открыть проект" : @"Открыть браузер";
    self.openButton.toolTip = nil;
    self.linkCopyButton.title = @"Скопировать ссылку";
    self.hidden = !url;
}
- (void)clear {
    self.url = nil;
    self.hidden = YES;
}
- (void)openClicked:(id)sender {
    if (!self.url) return;
    BOOL opened = self.launchURL ? self.launchURL(self.url) : [NSWorkspace.sharedWorkspace openURL:self.url];
    if (!opened) {
        self.openButton.title = @"Не удалось открыть";
        self.openButton.toolTip = @"Скопируйте ссылку и откройте её в выбранном браузере.";
    }
}
- (void)copyClicked:(id)sender {
    if (!self.url) return;
    [NSPasteboard.generalPasteboard clearContents];
    BOOL copied = [NSPasteboard.generalPasteboard setString:self.url.absoluteString forType:NSPasteboardTypeString];
    self.linkCopyButton.title = copied ? @"Ссылка скопирована" : @"Повторить копирование";
}
+ (int)selfTest {
    GABrowserActions *actions = [[self alloc] initWithFrame:NSMakeRect(0, 0, 464, 34)];
    __block NSUInteger launches = 0;
    actions.launchURL = ^BOOL(NSURL *url) { launches++; return YES; };
    if (!actions.hidden) return 1;
    [actions presentURL:[NSURL URLWithString:@"http://127.0.0.1:18180/connect/autocad/test"] project:NO];
    if (launches || actions.hidden || ![actions.openButton.title isEqual:@"Открыть браузер"]) return 2;
    [actions openClicked:nil];
    if (launches != 1) return 3;
    [actions clear];
    [actions openClicked:nil];
    if (!actions.hidden || launches != 1) return 4;
    [actions presentURL:[NSURL URLWithString:@"http://127.0.0.1:18180/projects/test/import"] project:YES];
    if (launches != 1 || ![actions.openButton.title isEqual:@"Открыть проект"]) return 5;
    actions.launchURL = ^BOOL(NSURL *url) { launches++; return NO; };
    [actions openClicked:nil];
    if (launches != 2 || ![actions.openButton.title isEqual:@"Не удалось открыть"] || actions.hidden) return 6;
    [actions clear];
    puts("browser_actions: 6 checks passed; no browser or clipboard touched");
    return 0;
}
@end
