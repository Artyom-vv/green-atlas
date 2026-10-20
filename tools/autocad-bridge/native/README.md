# Native bridge: границы модулей

AutoCAD/ObjectARX читает CAD; это не самостоятельный DWG/DXF-парсер.
Общие правила задаёт [CAD-контракт](../../../docs/architecture/autocad-contract.md).

| Ответственность | Файлы |
|---|---|
| Регистрация и команды | `green_atlas_bridge.cpp`, `capture_commands.*` |
| Области и HATCH | `region_extraction.cpp`, `hatch_extraction.cpp`, `hatch_loop_roles.*` |
| Кривые и точность | `curve_extraction.*`, `curve_sampling.*`, `geometry_math.*` |
| Экземпляры и обход | `drawing_traversal.*`, `block_traversal.cpp`, `entity_extraction.cpp` |
| XREF и зависимости | `xref_resolver.*`, `reference_search.*` |
| Снимок и запись | `snapshot_writer.cpp`, `file_io.*` |
| Отмена и прогресс | `operation_control.*` |
| Пользовательская передача | `delivery_command.*`, `delivery_recovery.*`, `delivery_ui.*` |

Объект AutoCAD закрывает владеющий им обход; обработчик не закрывает переданный
ему объект повторно. Кеш внешних баз живёт весь захват. Предыдущая working
database и callbacks восстанавливаются, включая исключения.
Доступ к базам AutoCAD не становится потокобезопасным из-за thread-local callback.

Каждый `.cpp` компилируется отдельно. Geometry и запись JSON не владеют UI,
назначениями слоёв или правилами размещения. Кандидат на замыкание не
становится расчётной площадью до предусмотренного подтверждения и подготовки.

Проверки без ObjectARX контролируют структуру/IO, но не доказывают чтение
объектов самим AutoCAD. Компиляция, native-запуск, полный Setup и установленный
пользовательский сценарий квалифицируются раздельно.
[Сборка Windows](../windows-2027/README.md),
[статус 133](<../../../docs/obsidian/04 Качество и показатели/Качество и показатели.md>),
[архив прежних native-проверок](../../../docs/archive/pre-133/tools/autocad-bridge/native/README.md).
