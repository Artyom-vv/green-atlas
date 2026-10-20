#import <Foundation/Foundation.h>
BOOL GAOwnedLauncher(NSURL *entry);
BOOL GAValidLauncher(NSURL *source, NSError **error);
BOOL GAInstallLauncher(NSURL *source, NSURL *applications, NSURL **backup, NSError **error);
BOOL GALauncherPreflight(NSURL *source, NSURL *applications, NSError **error);
