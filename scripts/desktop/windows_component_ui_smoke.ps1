[CmdletBinding()]
param([long]$WindowHandle, [int]$AppProcessId, [string]$FixtureRoot, [string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '../windows-env.ps1')
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class GreenAtlasPickerNative {
    [DllImport("user32.dll")] public static extern IntPtr GetDlgItem(IntPtr dialog, int id);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "SendMessageTimeoutW")]
    public static extern IntPtr SendText(IntPtr window, uint message, IntPtr wparam, string value, uint flags, uint timeout, out IntPtr result);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "SendMessageTimeoutW")]
    public static extern IntPtr ReadText(IntPtr window, uint message, IntPtr wparam, StringBuilder value, uint flags, uint timeout, out IntPtr result);
    [DllImport("user32.dll", ExactSpelling = true)] public static extern bool PostMessageW(IntPtr window, uint message, IntPtr wparam, IntPtr lparam);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extraInfo);
}
'@
$scope = [System.Windows.Automation.TreeScope]::Descendants
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
$steps = [Collections.Generic.List[string]]::new()

function Find-Control($Parent, [string]$Name, $Type, [int]$Seconds = 25, [bool]$AllowOffscreen = $false) {
    $condition = [System.Windows.Automation.AndCondition]::new(
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty, $Name),
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty, $Type))
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $found = $Parent.FindFirst($scope, $condition)
        if ($null -ne $found -and ($AllowOffscreen -or -not $found.Current.IsOffscreen)) { return $found }
        Start-Sleep -Milliseconds 150
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "UIA control missing: $Name"
}
function Button($Parent, [string]$Name) {
    $control = Find-Control $Parent $Name ([System.Windows.Automation.ControlType]::Button)
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while (-not $control.Current.IsEnabled -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 150 }
    if (-not $control.Current.IsEnabled) { throw "UIA button disabled: $Name" }
    ([System.Windows.Automation.InvokePattern]$control.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)).Invoke()
}
function Edit($Name) { return Find-Control $root $Name ([System.Windows.Automation.ControlType]::Edit) }
function Set-Value($Control, [string]$Value) {
    ([System.Windows.Automation.ValuePattern]$Control.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)).SetValue($Value)
}
function Value($Control) { return ([System.Windows.Automation.ValuePattern]$Control.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)).Current.Value }
function Observe([string]$Name, [long]$Handle = $WindowHandle) {
    $steps.Add($Name)
    @{ name=$Name; handle=$Handle } | ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $OutputDirectory 'component-step.json') -Encoding UTF8
    $ack = Join-Path $OutputDirectory ($Name + '.ack')
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Test-Path -LiteralPath $ack) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
    if (-not (Test-Path -LiteralPath $ack)) { throw "No screenshot acknowledgement: $Name" }
}
function Find-Dialog([string]$Title) {
    $condition = [System.Windows.Automation.AndCondition]::new(
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty, $Title),
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Window))
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        # The shell picker can be a descendant of the owned WebView host rather
        # than a RootElement child. Searching under our verified app window
        # preserves the ownership boundary across both UIA layouts.
        $dialog = $root.FindFirst($scope, $condition)
        if ($null -ne $dialog -and $dialog.Current.NativeWindowHandle -ne 0) { return $dialog }
        Start-Sleep -Milliseconds 150
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Native picker missing: $Title"
}
function Assert-ElementInPicker($Dialog, $Element) {
    $cursor = $Element
    $walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
    for ($depth = 0; $null -ne $cursor -and $depth -lt 64; $depth++) {
        if ($cursor.Equals($Dialog)) { return }
        $cursor = $walker.GetParent($cursor)
    }
    throw 'UIA element does not belong to the owned native picker'
}
function Assert-PickerFocus($Dialog) {
    $handle = [IntPtr]([long]$Dialog.Current.NativeWindowHandle)
    $dialogProcessId = [uint32]0
    [void][GreenAtlasPickerNative]::GetWindowThreadProcessId($handle, [ref]$dialogProcessId)
    if ($dialogProcessId -ne [uint32]$AppProcessId -or [GreenAtlasPickerNative]::GetForegroundWindow() -ne $handle) {
        throw 'Native picker lost the foreground or its expected process'
    }
    Assert-ElementInPicker $Dialog ([System.Windows.Automation.AutomationElement]::FocusedElement)
}
function Click-PickerElement($Dialog, $Element) {
    Assert-ElementInPicker $Dialog $Element
    Assert-PickerFocus $Dialog
    $box = $Element.Current.BoundingRectangle
    if ($box.IsEmpty -or $box.Width -lt 20 -or $box.Height -lt 10) { throw 'Native picker target has no usable bounds' }
    $x = [int]($box.X + $box.Width / 2)
    $y = [int]($box.Y + $box.Height / 2)
    $dialogBox = $Dialog.Current.BoundingRectangle
    if (-not $dialogBox.Contains([double]$x, [double]$y)) { throw 'Native picker target lies outside dialog' }
    if (-not [GreenAtlasPickerNative]::SetCursorPos($x, $y)) { throw 'Cannot move cursor onto native picker target' }
    [GreenAtlasPickerNative]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
    [GreenAtlasPickerNative]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
}
function Dialog-Button($Dialog, [string]$Id) {
    Assert-PickerFocus $Dialog
    $names = if ($Id -eq '1') { @('Выбор папки', 'Открыть', 'Select Folder', 'Open') } else { @('Отмена', 'Cancel') }
    foreach ($name in $names) {
        $target = $Dialog.FindFirst($scope, [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::NameProperty, $name))
        if ($null -ne $target -and $target.Current.ControlType -in @([System.Windows.Automation.ControlType]::Pane, [System.Windows.Automation.ControlType]::Button)) {
            Click-PickerElement $Dialog $target
            return
        }
    }
    $nativeButton = [GreenAtlasPickerNative]::GetDlgItem([IntPtr]([long]$Dialog.Current.NativeWindowHandle), [int]$Id)
    if ($nativeButton -ne [IntPtr]::Zero -and [GreenAtlasPickerNative]::PostMessageW([IntPtr]([long]$nativeButton), 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)) { return }
    $condition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty, $Id)
    $button = $Dialog.FindFirst($scope, $condition)
    $pattern = $null
    if ($null -ne $button -and $button.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
        ([System.Windows.Automation.InvokePattern]$pattern).Invoke()
    } else {
        # The Windows 11 shell exposes these native buttons as UIA Panes
        # without InvokePattern. Return/Escape activate their standard actions.
        if ($Id -eq '1') { Assert-PickerFocus $Dialog; [System.Windows.Forms.SendKeys]::SendWait('{ENTER}') }
        elseif ($Id -eq '2') { Assert-PickerFocus $Dialog; [System.Windows.Forms.SendKeys]::SendWait('{ESC}') }
        else { throw "Unsupported native picker button: $Id" }
    }
}
function Wait-PickerClosed([int]$Handle) {
    $condition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::NativeWindowHandleProperty, $Handle)
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        $remaining = $root.FindFirst($scope, $condition)
        if ($null -eq $remaining) { return }
        Start-Sleep -Milliseconds 150
    } while ([DateTime]::UtcNow -lt $deadline)
    Observe 'picker-still-open' $Handle
    throw 'Native picker did not close after accept or cancel'
}
function Set-PickerFieldByClick($Dialog, [string]$FieldName, [string]$Path) {
    $condition = [System.Windows.Automation.AndCondition]::new(
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty, $FieldName),
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Pane))
    $field = $Dialog.FindFirst($scope, $condition)
    if ($null -eq $field) { return $false }
    Click-PickerElement $Dialog $field
    Assert-PickerFocus $Dialog
    $keys = [regex]::Replace($Path, '([+^%~(){}])', '{$1}')
    Assert-PickerFocus $Dialog
    [System.Windows.Forms.SendKeys]::SendWait($keys)
    return $true
}
function Choose([string]$Field, [string]$Title, [string]$Path, [string]$Step) {
    Button $root ('Выбрать: ' + $Field)
    $dialog = Find-Dialog $Title
    $dialogHandle = $dialog.Current.NativeWindowHandle
    if ($Step) { Observe $Step $dialog.Current.NativeWindowHandle }
    if ($Path -eq '') { Dialog-Button $dialog '2'; Wait-PickerClosed $dialogHandle; return }
    # On Windows 11 the visible bottom input is a Pane without ValuePattern.
    # Setting a different edit through UIA leaves this field visibly empty.
    $nativeFieldName = if ($Title -eq 'Выберите папку компонента') { 'Папка:' } else { 'Имя файла:' }
    if (Set-PickerFieldByClick $dialog $nativeFieldName $Path) {
        Dialog-Button $dialog '1'
        Wait-PickerClosed $dialogHandle
        return
    }
    $edits = $dialog.FindAll($scope, [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit))
    $edit = @($edits | Where-Object { $_.Current.Name -match '^(File name|Folder|Имя файла|Папка):?$' -or $_.Current.AutomationId -eq '1148' } | Select-Object -Last 1)
    if ($edit.Count -ne 1) {
        # Windows 11 can expose the bottom folder field as a ComboBox/Panes
        # rather than Edit. Prefer the common-dialog ID, then its focused field.
        $candidate = $dialog.FindFirst($scope, [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::AutomationIdProperty, '1148'))
        if ($null -eq $candidate) {
            Assert-PickerFocus $dialog
            $candidate = [System.Windows.Automation.AutomationElement]::FocusedElement
            Assert-ElementInPicker $dialog $candidate
        }
        $pattern = $null
        if ($null -ne $candidate -and $candidate.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) {
            $edit = @($candidate)
        } else {
            # The focused folder field can be a shell Pane (ID 1152) without
            # ValuePattern. Prefer its native HWND for exact Unicode input;
            # only fall back to keys after verifying focus belongs to this dialog.
            Assert-PickerFocus $dialog
            $field = [GreenAtlasPickerNative]::GetDlgItem([IntPtr]([long]$dialogHandle), 1152)
            if ($field -eq [IntPtr]::Zero -and $null -ne $candidate -and $candidate.Current.NativeWindowHandle -ne 0) {
                $field = [IntPtr]([long]$candidate.Current.NativeWindowHandle)
            }
            $setNatively = $false
            if ($field -ne [IntPtr]::Zero) {
                $fieldProcessId = [uint32]0
                [void][GreenAtlasPickerNative]::GetWindowThreadProcessId($field, [ref]$fieldProcessId)
                if ($fieldProcessId -ne [uint32]$AppProcessId) { throw 'Native picker field belongs to another process' }
                $result = [IntPtr]::Zero
                $sent = [GreenAtlasPickerNative]::SendText($field, 0x000C, [IntPtr]::Zero, $Path, 0x0002, 1000, [ref]$result)
                if ($sent -ne [IntPtr]::Zero -and $result -ne [IntPtr]::Zero) {
                    $value = [Text.StringBuilder]::new(32768)
                    $readResult = [IntPtr]::Zero
                    $read = [GreenAtlasPickerNative]::ReadText($field, 0x000D, [IntPtr]$value.Capacity, $value, 0x0002, 1000, [ref]$readResult)
                    $setNatively = $read -ne [IntPtr]::Zero -and $value.ToString() -eq $Path
                }
            }
            if (-not $setNatively) {
                Assert-PickerFocus $dialog
                [System.Windows.Forms.SendKeys]::SendWait('^a')
                $keys = [regex]::Replace($Path, '([+^%~(){}])', '{$1}')
                Assert-PickerFocus $dialog
                [System.Windows.Forms.SendKeys]::SendWait($keys)
            }
            Dialog-Button $dialog '1'
            Wait-PickerClosed $dialogHandle
            return
        }
    }
    Set-Value $edit[0] $Path
    Dialog-Button $dialog '1'
    Wait-PickerClosed $dialogHandle
}
function Expect-Value([string]$Field, [string]$Expected) {
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while ((Value (Edit $Field)) -ne $Expected -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 150 }
    if ((Value (Edit $Field)) -ne $Expected) { throw ('Native picker changed the wrong field or lost the path: ' + $Field) }
}
function Cancel-Preserves([string]$Field, [string]$Title) {
    $before = Value (Edit $Field)
    Choose $Field $Title '' ''
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Edit $Field).Current.IsEnabled -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 150 }
    if (-not (Edit $Field).Current.IsEnabled) { throw ('Native picker did not close: ' + $Field) }
    Expect-Value $Field $before
}

$receipt = @{ passed=$false; fixture_libraries=$true; actual_autocad=$false; actual_blender=$false }
try {
    Button $root 'Компоненты'
    $null = Edit 'AutoCAD 2027'
    $null = Edit 'Шаблон AutoCAD'
    $null = Edit 'Blender'
    $null = Edit 'Библиотека Botaniq'
    $null = Edit 'PBR-текстуры'
    Observe '08-component-settings'
    # Only test picker transport with these fixtures. Restore any discovered CAD
    # paths before validation; these files cannot qualify an AutoCAD installation.
    $cadBefore = Value (Edit 'AutoCAD 2027')
    $templateBefore = Value (Edit 'Шаблон AutoCAD')
    $cadFixture = Join-Path $FixtureRoot 'AutoCAD fixture'
    $templateFixture = Join-Path $cadFixture 'Шаблон проверки.dwt'
    Choose 'AutoCAD 2027' 'Выберите папку компонента' $cadFixture '09-autocad-picker-accept'
    Expect-Value 'AutoCAD 2027' $cadFixture
    Expect-Value 'Шаблон AutoCAD' $templateBefore
    Cancel-Preserves 'AutoCAD 2027' 'Выберите папку компонента'
    Choose 'Шаблон AutoCAD' 'Выберите файл компонента' $templateFixture '10-template-picker-accept'
    Expect-Value 'Шаблон AutoCAD' $templateFixture
    Expect-Value 'AutoCAD 2027' $cadFixture
    Cancel-Preserves 'Шаблон AutoCAD' 'Выберите файл компонента'
    Observe '10b-cad-picker-roundtrips'
    Set-Value (Edit 'AutoCAD 2027') $cadBefore
    Set-Value (Edit 'Шаблон AutoCAD') $templateBefore
    Expect-Value 'AutoCAD 2027' $cadBefore
    Expect-Value 'Шаблон AutoCAD' $templateBefore
    Choose 'Blender' 'Выберите файл компонента' (Join-Path $FixtureRoot 'blender.exe') '11-blender-picker'
    Expect-Value 'Blender' (Join-Path $FixtureRoot 'blender.exe')
    Cancel-Preserves 'Blender' 'Выберите файл компонента'
    Button $root 'Сохранить'
    $invalidStarted = [DateTime]::UtcNow
    try {
        $null = Find-Control $root 'Исправьте отмеченные пути или очистите поле, чтобы подключить компонент позже.' ([System.Windows.Automation.ControlType]::Text) 60 $true
    } catch {
        $receipt.invalid_save_wait_seconds = [math]::Round(([DateTime]::UtcNow - $invalidStarted).TotalSeconds, 1)
        Observe '12-invalid-blender-timeout'
        throw
    }
    $receipt.invalid_save_wait_seconds = [math]::Round(([DateTime]::UtcNow - $invalidStarted).TotalSeconds, 1)
    Observe '12-invalid-blender-rejected'
    Set-Value (Edit 'Blender') ''
    Choose 'Библиотека Botaniq' 'Выберите папку компонента' $FixtureRoot '13-botaniq-picker'
    Expect-Value 'Библиотека Botaniq' $FixtureRoot
    Cancel-Preserves 'Библиотека Botaniq' 'Выберите папку компонента'
    Choose 'PBR-текстуры' 'Выберите папку компонента' (Join-Path $FixtureRoot 'pbr') '14-pbr-picker'
    Expect-Value 'PBR-текстуры' (Join-Path $FixtureRoot 'pbr')
    Cancel-Preserves 'PBR-текстуры' 'Выберите папку компонента'
    Button $root 'Сохранить'
    $null = Find-Control $root 'Настройки сохранены. Новые задания используют выбранные пути.' ([System.Windows.Automation.ControlType]::Text) 25 $true
    Observe '15-component-settings-saved'
    $botaniq = Value (Edit 'Библиотека Botaniq')
    $pbr = Value (Edit 'PBR-текстуры')
    if ($botaniq -ne (Join-Path $FixtureRoot 'botaniq_full') -or $pbr -ne (Join-Path $FixtureRoot 'pbr')) { throw 'Native library roots were not normalized' }
    # Header close icon and footer button have the same accessible name; either
    # closes only the settings dialog, leaving the project catalogue intact.
    Button $root 'Закрыть'
    Button $root 'Компоненты'
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while ((Value (Edit 'Библиотека Botaniq')) -ne $botaniq -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 150 }
    if ((Value (Edit 'Библиотека Botaniq')) -ne $botaniq -or (Value (Edit 'PBR-текстуры')) -ne $pbr) { throw 'Saved settings did not survive reopening' }
    Observe '16-component-settings-reopened'
    Button $root 'Закрыть'
    $receipt.passed = $true
    $receipt.native_pickers = @('autocad_root','autocad_template','blender','botaniq_root','pbr_root')
    $receipt.native_picker_accept_fields = $receipt.native_pickers
    $receipt.native_picker_cancel_preserved_fields = $receipt.native_pickers
    $receipt.blender_invalid_rejected = $true
    $receipt.reopened_settings_equal = $true
} catch {
    $receipt.error = $_.Exception.Message
    try { $receipt.controls = @($root.FindAll($scope, [System.Windows.Automation.Condition]::TrueCondition) | Select-Object -First 140 | ForEach-Object { @{ name=$_.Current.Name; type=$_.Current.ControlType.ProgrammaticName } }) } catch {}
} finally {
    $receipt.steps = @($steps)
    $receipt | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'component-ui-receipt.json') -Encoding UTF8
}
if (-not $receipt.passed) { throw $receipt.error }
