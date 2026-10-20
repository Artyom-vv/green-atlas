#import <AppKit/AppKit.h>
// Present the authenticated journal without starting an import. Return only a
// chosen receipt; the host decides whether to open it or explicitly resume it.
void GAChooseHandoff(NSWindow *window, NSArray *receipts, void (^selected)(NSDictionary *));
