#import <Foundation/Foundation.h>

// Возвращённый fd удерживается до конца установки и закрывается вызывающим кодом.
// Отсутствующий профиль не создаётся. Содержимое lock-файла не читается.
BOOL GAAcquireWorkspaceInstallLease(NSURL *workspace, int *descriptor, NSError **error);
BOOL GAExactExecutableIsRunning(NSURL *executable);
