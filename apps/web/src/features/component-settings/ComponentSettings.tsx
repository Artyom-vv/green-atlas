import { Button, Dialog, TextInput } from '@green/ui';
import { Settings2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { desktopBridge } from '@/shared/desktop/desktopBridge';

const fields = [
  [
    'autocad_root',
    'AutoCAD 2027',
    'Папка установленного AutoCAD. Нужен для подготовки чертежа и CAD-выпуска.',
  ],
  [
    'autocad_template',
    'Шаблон AutoCAD',
    'Файл .dwt. Обычно находится автоматически; можно выбрать свой шаблон.',
  ],
  [
    'blender',
    'Blender',
    'Исполняемый файл Blender. Нужен для рендера и подготовки моделей просмотра.',
  ],
  [
    'botaniq_root',
    'Библиотека Botaniq',
    'Папка с blends_280 и textures. Можно выбрать родительскую папку пакета.',
  ],
  [
    'pbr_root',
    'PBR-текстуры',
    'Папка с текстурами материалов, используемыми Green Atlas.',
  ],
] as const;
type Field = (typeof fields)[number][0];
type Paths = Record<Field, string>;
type Checks = Partial<
  Record<Field, { ok: boolean; state: string; message: string }>
>;
const empty = Object.fromEntries(fields.map(([field]) => [field, ''])) as Paths;

export function ComponentSettings() {
  const [available, setAvailable] = useState(false);
  const [open, setOpen] = useState(false);
  const [paths, setPaths] = useState<Paths>(empty);
  const [checks, setChecks] = useState<Checks>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    void fetch('/_desktop/dependencies', {
      signal: controller.signal,
      credentials: 'same-origin',
    })
      .then(async (response) => {
        if (response.ok && (await response.json()).paths) setAvailable(true);
      })
      .catch(() => {});
    return () => controller.abort();
  }, []);
  const find = async (initial = false) => {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const response = await fetch('/_desktop/dependencies', {
        credentials: 'same-origin',
      });
      if (!response.ok) throw new Error('Не удалось найти компоненты');
      const result = (await response.json()) as {
        paths: Paths;
        warning?: string;
      };
      setPaths((previous) =>
        initial
          ? result.paths
          : (Object.fromEntries(
              fields.map(([field]) => [
                field,
                previous[field] || result.paths[field],
              ]),
            ) as Paths),
      );
      setChecks({});
      setMessage(
        result.warning ||
          'Поиск завершён. Выберите недостающие компоненты или подключите их позже.',
      );
    } catch {
      setError('Не удалось найти компоненты. Повторите попытку.');
    } finally {
      setBusy(false);
    }
  };
  const update = (field: Field, value: string) => {
    setPaths((previous) => ({ ...previous, [field]: value }));
    setChecks({});
    setMessage('');
    setError('');
  };
  const submit = async (save: boolean) => {
    setBusy(true);
    setMessage('');
    setError('');
    try {
      const response = await fetch(
        '/_desktop/dependencies/' + (save ? 'save' : 'check'),
        {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(paths),
        },
      );
      const result = (await response.json()) as {
        paths?: Paths;
        checks?: Checks;
        valid?: boolean;
        message?: string;
      };
      if (result.paths) setPaths(result.paths);
      setChecks(result.checks || {});
      if (!response.ok || !result.valid)
        setError(
          result.message ||
            'Исправьте отмеченные пути или очистите поле, чтобы подключить компонент позже.',
        );
      else
        setMessage(
          save
            ? 'Настройки сохранены. Новые задания используют выбранные пути.'
            : 'Пути проверены. Нажмите «Сохранить», чтобы применить их.',
        );
    } catch {
      setError(
        'Не удалось получить результат. Проверьте пути повторно перед сохранением.',
      );
    } finally {
      setBusy(false);
    }
  };
  const browse = async (field: Field) => {
    setBusy(true);
    try {
      const selected = await desktopBridge.chooseDependency(field);
      if (selected) update(field, selected);
    } finally {
      setBusy(false);
    }
  };
  if (!available) return null;
  return (
    <>
      <Button
        variant="ghost"
        startIcon={<Settings2 />}
        onClick={() => {
          setOpen(true);
          void find(true);
        }}
      >
        Компоненты
      </Button>
      <Dialog
        open={open}
        title="Компоненты и ресурсы"
        size="wide"
        closeDisabled={busy}
        onClose={() => setOpen(false)}
        footer={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              className="mr-auto"
              variant="ghost"
              disabled={busy}
              onClick={() => setOpen(false)}
            >
              Закрыть
            </Button>
            <Button disabled={busy} onClick={() => void submit(false)}>
              Проверить пути
            </Button>
            <Button
              variant="primary"
              disabled={busy}
              onClick={() => void submit(true)}
            >
              Сохранить
            </Button>
          </div>
        }
      >
        <div className="flex flex-col gap-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <p className="max-w-110 text-sm text-neutral-600">
              Выберите установленные программы и свои библиотеки. Пустое поле
              позволяет подключить компонент позже. Проекты и исходные файлы не
              перемещаются.
            </p>
            <Button disabled={busy} onClick={() => void find()}>
              Найти автоматически
            </Button>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {fields.map(([field, label, hint]) => (
              <div
                key={field}
                className={
                  'flex min-w-0 flex-col gap-1.5 ' +
                  (field === 'blender' ? 'md:col-span-2' : '')
                }
              >
                <label
                  htmlFor={'component-' + field}
                  className="text-sm font-medium"
                >
                  {label}
                </label>
                <div className="flex items-center gap-2">
                  <TextInput
                    id={'component-' + field}
                    className="min-w-0 flex-1"
                    value={paths[field]}
                    disabled={busy}
                    aria-describedby={'component-help-' + field}
                    aria-invalid={checks[field]?.ok === false}
                    placeholder="Не подключено"
                    onChange={(event) => update(field, event.target.value)}
                  />
                  {desktopBridge.canChooseDependency && (
                    <Button
                      disabled={busy}
                      aria-label={'Выбрать: ' + label}
                      onClick={() => void browse(field)}
                    >
                      Выбрать…
                    </Button>
                  )}
                </div>
                <p
                  id={'component-help-' + field}
                  className="text-xs text-neutral-600"
                >
                  {hint}
                </p>
                {checks[field] && (
                  <p
                    className={
                      'text-sm ' +
                      (checks[field]?.ok ? 'text-green-800' : 'text-red-700')
                    }
                  >
                    {checks[field]?.message}
                  </p>
                )}
              </div>
            ))}
          </div>
          {busy && (
            <p role="status" className="text-sm">
              Проверяем компоненты…
            </p>
          )}
          {error && (
            <p role="alert" className="text-sm text-red-700">
              {error}
            </p>
          )}
          {message && (
            <p role="status" className="text-sm text-neutral-700">
              {message}
            </p>
          )}
        </div>
      </Dialog>
    </>
  );
}
