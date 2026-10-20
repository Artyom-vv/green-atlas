#import <Foundation/Foundation.h>

// Отдельный профиль приёмки не подменяет HOME и никогда не выбирается по умолчанию.
BOOL GACreateQualificationRoot(NSURL *root, NSError **error);
NSDictionary *GAReadQualificationRoot(NSURL *root, NSError **error);
BOOL GAWriteQualificationRoot(NSURL *root, NSDictionary *state, NSError **error);
BOOL GAQualificationChild(NSURL *root, NSURL *child);
