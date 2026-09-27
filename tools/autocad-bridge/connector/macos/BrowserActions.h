#import <AppKit/AppKit.h>

// Receiving a link never launches a browser. Opening is an explicit UI action.
@interface GABrowserActions : NSView
@property(copy) BOOL (^launchURL)(NSURL *url);
- (void)presentURL:(NSURL *)url project:(BOOL)project;
- (void)clear;
+ (int)selfTest;
@end
