#import <Foundation/Foundation.h>

// Явный одноразовый профиль приёмки production-приложения; обычный запуск
// не меняется. Корень создаёт отдельная приёмка настоящего установщика.
NS_ASSUME_NONNULL_BEGIN
@interface GADesktopQualification : NSObject
@property(readonly, strong) NSURL *profile;
@property(readonly, strong) NSURL *receipt;
@property(readonly) BOOL finished;
@property(readonly) BOOL passed;
- (NSDictionary<NSString *, NSString *> *)runtimeEnvironment;
- (BOOL)finish:(NSDictionary *)proof runtimePID:(pid_t)pid
    runtimeExit:(int)status error:(nullable NSString *)failure;
@end

// nil без error означает обычный запуск. Проверка выполняется до NSApplication.
GADesktopQualification * _Nullable GAParseDesktopQualification(
    int argc, const char * _Nonnull const * _Nonnull argv, NSError * _Nullable * _Nullable error);
void GAConfigureDesktopQualificationPreferences(void);
NSString *GADesktopQualificationJavaScript(void);
BOOL GADesktopQualificationWebProof(id _Nullable value);
NS_ASSUME_NONNULL_END
