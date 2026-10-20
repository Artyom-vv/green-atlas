# AutoCAD bridge Green Atlas

Интеграция открытого AutoCAD с локальным приложением. AutoCAD читает чертёж,
подготавливает геометрию и создаёт отдельный выпуск. Посадки рассчитываются
в Green Atlas по сохранённому снимку.

Текущая поставка: **Windows 0.1.133, AutoCAD 2027 x64**.
GUI-плагин 0.1.48 и Core worker 0.1.49 имеют независимые версии.
[Установка и первый проект](../../submission/prototype/README.md).

## Карта кода

| Каталог | Назначение |
|---|---|
| `native` | Захват, объекты, XREF, геометрия и происхождение |
| `desktop` | Окно, приватный локальный runtime и передача проекта |
| `installer` | Установка, компоненты, обновление и удаление |
| `windows-2027` | Windows-адаптеры и инструкции AutoCAD 2027 |
| `diagnostics` | Изолированные проверки; не пользовательский импорт |

Команда пользователя — `GAOPEN`. DXF/ZIP-upload и старый connector не являются
вторым путём создания проекта. Исходные DWG не перезаписываются. Неполные XREF
и непрочитанные объекты сохраняются как замечания.

## Разработка

Сначала [CAD-контракт](../../docs/architecture/autocad-contract.md).
Перед CAD-правками: `python3 scripts/architecture/cad_dependencies.py --check`.
В Windows перед каждой командой подключается `scripts/windows-env.ps1`.

- [Полная сборка Windows](WINDOWS-PACKAGING.md).
- [Native-плагин Windows](windows-2027/README.md), [desktop host](desktop/WINDOWS.md).
- [Границы native-модулей](native/README.md), [диагностика](diagnostics/README.md).
- Mac-сборщик: `scripts/desktop/build_macos_product.py`; наличие сборщика не
  подтверждает поставку Mac 133. Квалификация платформ выполняется раздельно.

[Проверки выпуска 133](<../../docs/obsidian/04 Качество и показатели/Качество и показатели.md>),
[архив версий и прежних инструкций](../../docs/archive/pre-133/tools/autocad-bridge/README.md).
