#import "mac-status-panel.h"

static constexpr CGFloat kPanelPadding = 20;
static constexpr CGFloat kProgressHeight = 50;
static constexpr CGFloat kFailureHeight = 156;
static constexpr CGFloat kButtonHeight = 30;
static constexpr CGFloat kButtonWidth = 180;
static constexpr CGFloat kButtonGap = 12;
static constexpr CGFloat kButtonBottom = 12;
static constexpr CGFloat kTitleHeight = 22;
static constexpr CGFloat kFailureTitleTop = 10;
static constexpr CGFloat kMessageTitleGap = 8;
static constexpr CGFloat kMessageButtonGap = 10;
static constexpr CGFloat kProgressTitleBottom = 16;
static constexpr CGFloat kProgressCardWidth = 640;
static constexpr CGFloat kProgressCardHeight = 244;
static constexpr CGFloat kSpinnerSize = 32;
static constexpr NSTimeInterval kElapsedRefreshSeconds = 1;
static constexpr CGFloat kHeadingHeight = 34;
static constexpr CGFloat kStageHeight = 24;
static constexpr CGFloat kProgressRowGap = 20;
static constexpr CGFloat kProgressTextGap = 8;
static constexpr CGFloat kCardRadius = 12;

@implementation GADesktopStatusPanel {
    NSScrollView *_messageScroll;
    NSTextView *_message;
    NSButton *_library;
    BOOL _errorVisible;
    BOOL _loading;
    NSProgressIndicator *_spinner;
    NSTextField *_heading;
    NSTextField *_elapsed;
    NSTextField *_hint;
    NSDate *_startedAt;
    NSTimer *_timer;
}
- (instancetype)initWithTarget:(id)target retry:(SEL)retry cancel:(SEL)cancel library:(SEL)library {
    self = [super initWithFrame:NSZeroRect];
    if (!self) return nil;
    self.autoresizingMask = NSViewWidthSizable | NSViewMinYMargin;
    _status = [NSTextField labelWithString:@"Запускаем Green Atlas"];
    _spinner = [[NSProgressIndicator alloc] initWithFrame:NSZeroRect];
    _spinner.style = NSProgressIndicatorStyleSpinning;
    _spinner.controlSize = NSControlSizeRegular;
    _spinner.displayedWhenStopped = NO;
    _heading = [NSTextField labelWithString:@"Открываем проект"];
    _heading.font = [NSFont systemFontOfSize:24 weight:NSFontWeightSemibold];
    _elapsed = [NSTextField labelWithString:@""];
    _elapsed.textColor = NSColor.secondaryLabelColor;
    _hint = [NSTextField labelWithString:@""];
    _hint.textColor = NSColor.secondaryLabelColor;
    _retry = [NSButton buttonWithTitle:@"Повторить" target:target action:retry];
    _cancel = [NSButton buttonWithTitle:@"Отменить" target:target action:cancel];
    _library = [NSButton buttonWithTitle:@"К проектам" target:target action:library];
    _messageScroll = [[NSScrollView alloc] initWithFrame:NSZeroRect];
    _messageScroll.hasVerticalScroller = YES;
    _messageScroll.drawsBackground = NO;
    _message = [[NSTextView alloc] initWithFrame:NSZeroRect];
    _message.editable = NO;
    _message.selectable = YES;
    _message.richText = NO;
    _message.drawsBackground = NO;
    _message.font = [NSFont systemFontOfSize:NSFont.systemFontSize];
    _message.textColor = NSColor.labelColor;
    _message.textContainerInset = NSMakeSize(0, 0);
    _message.textContainer.lineFragmentPadding = 0;
    _message.verticallyResizable = YES;
    _message.horizontallyResizable = NO;
    _message.minSize = NSMakeSize(0, 0);
    _message.maxSize = NSMakeSize(CGFLOAT_MAX, CGFLOAT_MAX);
    _message.autoresizingMask = NSViewWidthSizable;
    _message.textContainer.widthTracksTextView = YES;
    _messageScroll.documentView = _message;
    for (NSView *view in @[_status, _retry, _cancel, _library, _messageScroll, _spinner, _heading, _elapsed, _hint]) [self addSubview:view];
    _retry.hidden = YES; _cancel.hidden = YES;
    [self clearError];
    return self;
}
- (void)dealloc { [_timer invalidate]; }
- (BOOL)isLoading { return _loading; }
- (void)updateElapsed {
    NSInteger seconds = MAX(0, (NSInteger)-_startedAt.timeIntervalSinceNow);
    _elapsed.stringValue = seconds < 60
        ? [NSString stringWithFormat:@"Прошло %ld с", (long)seconds]
        : [NSString stringWithFormat:@"Прошло %ld мин %ld с", (long)(seconds / 60), (long)(seconds % 60)];
}
- (void)stopProgress {
    [_timer invalidate]; _timer = nil;
    [_spinner stopAnimation:nil];
    _loading = NO;
    for (NSView *view in @[_spinner, _heading, _elapsed, _hint]) view.hidden = YES;
    _status.font = [NSFont systemFontOfSize:NSFont.systemFontSize];
    self.needsDisplay = YES;
}
- (void)presentProgress:(NSString *)stage autocadRequired:(BOOL)required {
    _errorVisible = NO;
    _messageScroll.hidden = YES; _library.hidden = YES; _retry.hidden = YES;
    _status.stringValue = stage;
    _status.font = [NSFont systemFontOfSize:16];
    _hint.stringValue = required ? @"Подготовка выполняется локально" : @"Чертёж передан, AutoCAD можно закрыть";
    if (!_loading) {
        _startedAt = [NSDate date];
        __weak GADesktopStatusPanel *weakSelf = self;
        _timer = [NSTimer timerWithTimeInterval:kElapsedRefreshSeconds repeats:YES block:^(NSTimer *) {
            [weakSelf updateElapsed];
        }];
        [NSRunLoop.mainRunLoop addTimer:_timer forMode:NSRunLoopCommonModes];
    }
    _loading = YES;
    for (NSView *view in @[_spinner, _heading, _elapsed, _hint]) view.hidden = NO;
    [_spinner startAnimation:nil];
    [self updateElapsed];
    self.needsLayout = YES; self.needsDisplay = YES;
}
- (CGFloat)preferredHeight { return _errorVisible ? kFailureHeight : kProgressHeight; }
- (void)presentFailure:(GAHandoffFailure *)failure runtimeAvailable:(BOOL)available {
    _status.stringValue = failure.title;
    [self showMessage:failure.message runtimeAvailable:available];
    _retry.title = @"Повторить передачу";
}
- (void)presentError:(NSString *)message runtimeAvailable:(BOOL)available {
    _status.stringValue = @"Не удалось завершить действие";
    [self showMessage:message runtimeAvailable:available];
    _retry.title = available ? @"Повторить" : @"Запустить сервис";
}
- (void)showMessage:(NSString *)message runtimeAvailable:(BOOL)available {
    [self stopProgress];
    _errorVisible = YES;
    _message.string = message;
    _messageScroll.hidden = NO;
    _library.hidden = !available;
    _retry.hidden = NO;
    _cancel.hidden = YES;
    self.needsLayout = YES;
}
- (void)clearError {
    [self stopProgress];
    _errorVisible = NO;
    _message.string = @"";
    _messageScroll.hidden = YES;
    _library.hidden = YES;
    _retry.title = @"Повторить";
    self.needsLayout = YES;
}
- (BOOL)isOpaque { return _loading; }
- (void)drawRect:(NSRect)dirtyRect {
    if (!_loading) return;
    [NSColor.windowBackgroundColor setFill]; NSRectFill(self.bounds);
    CGFloat width = MIN(kProgressCardWidth, NSWidth(self.bounds) - 2 * kPanelPadding);
    NSRect card = NSMakeRect((NSWidth(self.bounds) - width) / 2,
        (NSHeight(self.bounds) - kProgressCardHeight) / 2, width, kProgressCardHeight);
    NSBezierPath *path = [NSBezierPath bezierPathWithRoundedRect:card xRadius:kCardRadius yRadius:kCardRadius];
    [NSColor.controlBackgroundColor setFill]; [path fill];
    [NSColor.separatorColor setStroke]; [path stroke];
}
- (void)layout {
    [super layout];
    CGFloat width = NSWidth(self.bounds);
    CGFloat height = NSHeight(self.bounds);
    if (_loading) {
        CGFloat cardWidth = MIN(kProgressCardWidth, width - 2 * kPanelPadding);
        CGFloat left = (width - cardWidth) / 2 + kPanelPadding;
        CGFloat bottom = (height - kProgressCardHeight) / 2;
        CGFloat textWidth = cardWidth - 2 * kPanelPadding;
        CGFloat row = bottom + kProgressCardHeight - kPanelPadding - kHeadingHeight;
        _heading.frame = NSMakeRect(left, row, textWidth, kHeadingHeight);
        row -= kProgressRowGap + kSpinnerSize;
        _spinner.frame = NSMakeRect(left, row, kSpinnerSize, kSpinnerSize);
        _status.frame = NSMakeRect(left + kSpinnerSize + kButtonGap,
            row + (kSpinnerSize - kStageHeight) / 2, textWidth - kSpinnerSize - kButtonGap, kStageHeight);
        row -= kProgressRowGap + kTitleHeight;
        _elapsed.frame = NSMakeRect(left, row, textWidth, kTitleHeight);
        row -= kProgressTextGap + kTitleHeight;
        _hint.frame = NSMakeRect(left, row, textWidth, kTitleHeight);
        _cancel.frame = NSMakeRect(left, bottom + kPanelPadding, kButtonWidth, kButtonHeight);
        return;
    }
    if (_errorVisible) {
        const CGFloat titleBottom = height - kFailureTitleTop - kTitleHeight;
        const CGFloat messageBottom = kButtonBottom + kButtonHeight + kMessageButtonGap;
        _status.frame = NSMakeRect(kPanelPadding, titleBottom, width - 2 * kPanelPadding, kTitleHeight);
        _messageScroll.frame = NSMakeRect(kPanelPadding, messageBottom,
            width - 2 * kPanelPadding, titleBottom - kMessageTitleGap - messageBottom);
        _message.frame = NSMakeRect(0, 0, NSWidth(_messageScroll.contentView.bounds), NSHeight(_messageScroll.contentView.bounds));
        _retry.frame = NSMakeRect(kPanelPadding, kButtonBottom, kButtonWidth, kButtonHeight);
        _library.frame = NSMakeRect(kPanelPadding + kButtonWidth + kButtonGap,
            kButtonBottom, kButtonWidth, kButtonHeight);
    } else {
        _status.frame = NSMakeRect(kPanelPadding, kProgressTitleBottom,
            width - kButtonWidth - 3 * kPanelPadding, kTitleHeight);
        _retry.frame = NSMakeRect(width - kPanelPadding - kButtonWidth,
            kButtonBottom, kButtonWidth, kButtonHeight);
    }
    _cancel.frame = _retry.frame;
}
@end
