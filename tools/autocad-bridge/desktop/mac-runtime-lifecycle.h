#import <Foundation/Foundation.h>

// Жизненный цикл дочернего API Mac: дождаться освобождения профиля и сохранить
// диагностику предыдущего запуска. Подготовка CAD и пользовательские данные здесь не меняются.
NS_ASSUME_NONNULL_BEGIN
void GAStopRuntime(NSTask * _Nullable task, NSTimeInterval timeout, void (^completed)(BOOL stopped));
NSFileHandle * _Nullable GAOpenRuntimeLog(NSURL *profile, NSError * _Nullable * _Nullable error);
NSFileHandle * _Nullable GARuntimeExitLog(NSFileHandle *log);
void GARecordRuntimeExit(NSFileHandle * _Nullable log, NSTask *task,
    BOOL expectedClosing, BOOL startupTimedOut);
NS_ASSUME_NONNULL_END
