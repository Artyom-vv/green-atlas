# Native-плагин для AutoCAD 2027 Windows

Сборщик `tools/autocad-bridge/build-windows-2027.ps1` компилирует GUI ARX или
Core CRX. Он не устанавливает продукт и не подтверждает запуск в AutoCAD.
В поставке 133 версии компонентов: GUI 0.1.48, Core 0.1.49.

## Среда и команды

Нужны PowerShell 7, MSVC x64 от 14.44.35207, Windows SDK и официальный
Windows ObjectARX SDK 2027. SDK и компилятор не скачиваются сборщиком.
Команды из корня checkout, в одной PowerShell-сессии:

```powershell
. ./scripts/windows-env.ps1
./tools/autocad-bridge/build-windows-2027.ps1 -Target Core -SdkRoot 'C:\SDK\ObjectARX2027' -ValidateOnly

. ./scripts/windows-env.ps1
./tools/autocad-bridge/build-windows-2027.ps1 -Target Core -SdkRoot 'C:\SDK\ObjectARX2027'

. ./scripts/windows-env.ps1
./tools/autocad-bridge/build-windows-2027.ps1 -Target Gui -SdkRoot 'C:\SDK\ObjectARX2027'
```

`ValidateOnly` проверяет предпосылки. `-Target` выбирает конкретный компонент;
полный установщик создаётся [общим сборщиком](../WINDOWS-PACKAGING.md).
Не переименовывайте worker или версию в metadata ради допуска несовместимого бинарника.

## Границы

GUI захватывает открытый чертёж, Core готовит снимок и отдельный выпуск.
Расчёт посадок локальный и общий. Требуются проверка manifest, загрузка
в AutoCAD, контроль XREF и повторное открытие CAD-результата.
Успешная линковка не является продуктовой приёмкой.

[CAD-контракт](../../../docs/architecture/autocad-contract.md),
[проверки 133](<../../../docs/obsidian/04 Качество и показатели/Качество и показатели.md>).
