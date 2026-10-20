# Сборка Windows Green Atlas

Полный сборщик: `tools/autocad-bridge/build-windows-product.ps1`.
Версия текущей поставки — 0.1.133; GUI 0.1.48, Core 0.1.49.
Не подменяйте файлы после создания manifest.

## Среда

Нужны Windows x64, PowerShell 7, MSVC x64 от 14.44.35207, Windows SDK,
ObjectARX SDK 2027, WebView2 SDK 1.0.4191.47, CPython по
`apps/api/.python-version`, окружение `uv.lock` и `desktop/build-requirements.txt`.
Node.js x64 должен удовлетворять требованиям pnpm из `package.json`;
зависимости устанавливаются по lockfile до сборки.

Подготовьте x64 CRT и полный Microsoft offline Evergreen installer WebView2
с известным SHA-256. Сборщик проверяет SHA и Microsoft Authenticode.
Минимальная версия Evergreen для выбранного SDK — 152.0.4191.53.
SDK и компилятор автоматически не скачиваются.

## Команды из корня

```powershell
. ./scripts/windows-env.ps1
$buildParams = @{
  SourceManifest = 'C:\work\source-manifest.json'
  SdkRoot = 'C:\SDK\ObjectARX2027'
  WebView2SdkRoot = 'C:\SDK\WebView2'
  VcRedistDirectory = 'C:\SDK\VC\Redist\x64\Microsoft.VC143.CRT'
  AutoCADRoot = 'C:\Program Files\Autodesk\AutoCAD 2027'
  WebView2Installer = 'C:\SDK\MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
  WebView2InstallerSha256 = '<SHA-256 локального Microsoft installer>'
  BuildPython = (Resolve-Path './apps/api/.venv/Scripts/python.exe').Path
}

. ./scripts/windows-env.ps1
./tools/autocad-bridge/build-windows-product.ps1 @buildParams -ValidateOnly

. ./scripts/windows-env.ps1
$setup = ./tools/autocad-bridge/build-windows-product.ps1 @buildParams
```

Source manifest создаётся для точного среза исходников; проверяется до компиляции.
`ValidateOnly` проверяет предпосылки, не собирает и не устанавливает продукт.
Обычная сборка создаёт компоненты в новом каталоге checkout `.runtime`, собирает
Web с пустым `VITE_API_URL`, упаковывает runtime, host, ARX/CRX и Setup.

Опциональная подпись: `-SigningCertificateThumbprint`. Без подписи продукт
остаётся неподписанным кандидатом. Microsoft CRT/WebView2 проверяются отдельно.

## Приёмка

Manifest связывает источник, компоненты, размеры и SHA. Сам по себе он
не подтверждает работу AutoCAD, WebView2 и Blender. Проверяются чистая установка,
обновление, сохранность профиля, CAD-путь, отмена, повторное открытие и рендер.
В поставке 133 native-бинарники переиспользованы из принятого Windows 131;
это не их повторная компиляция с новым номером Setup.

[Статус приёмки 133](<../../docs/obsidian/04 Качество и показатели/Качество и показатели.md>),
[установка пользователем](windows-2027/INSTALL-WINDOWS.md),
[сборочная проверка](../../scripts/desktop/README.md).
