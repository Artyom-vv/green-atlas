#import <AppKit/AppKit.h>
#import "handoff-failure.h"

// Представление локальной передачи: этап, время ожидания и восстановление
// Ошибка сохраняет причину, а web-документ и его черновик остаются смонтированными
@interface GADesktopStatusPanel : NSView
@property(strong, readonly) NSTextField *status;
@property(strong, readonly) NSButton *retry;
@property(strong, readonly) NSButton *cancel;
@property(readonly) CGFloat preferredHeight;
@property(readonly, getter=isLoading) BOOL loading;
- (void)presentProgress:(NSString *)stage autocadRequired:(BOOL)required;
- (instancetype)initWithTarget:(id)target retry:(SEL)retry cancel:(SEL)cancel library:(SEL)library;
- (void)presentFailure:(GAHandoffFailure *)failure runtimeAvailable:(BOOL)available;
- (void)presentError:(NSString *)message runtimeAvailable:(BOOL)available;
- (void)clearError;
@end
