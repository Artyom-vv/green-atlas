import type { LocationServiceSettings } from '@green/api-client';
import { Button, Field, TextInput } from '@green/ui';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { locationServiceSettingsActions } from '../model/locationServiceSettingsActions';
const sourceLabel: Record<LocationServiceSettings['source'], string> = {
  personal: 'Личный ключ',
  environment: 'Ключ окружения',
  bundled: 'Ключ приложения',
  none: 'Не настроено',
};

export function LocationServiceSettingsForm({
  onChanged,
}: {
  onChanged: (settings: LocationServiceSettings) => void;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-4 border-t border-neutral-200 pt-3">
      <Button
        variant="ghost"
        controlSize="compact"
        aria-expanded={open}
        aria-controls={id}
        endIcon={open ? <ChevronUp /> : <ChevronDown />}
        onClick={() => setOpen((value) => !value)}
      >
        Настройки поиска адресов
      </Button>
      {open && (
        <SettingsEditor
          id={id}
          onChanged={onChanged}
          onCancel={() => setOpen(false)}
        />
      )}
    </div>
  );
}

function SettingsEditor({
  id,
  onChanged,
  onCancel,
}: {
  id: string;
  onChanged: (settings: LocationServiceSettings) => void;
  onCancel: () => void;
}) {
  const [settings, setSettings] = useState<LocationServiceSettings>();
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState<
    'loading' | 'saving' | 'resetting' | undefined
  >('loading');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [retry, setRetry] = useState(0);
  const controller = useRef<AbortController | undefined>(undefined);
  const generation = useRef(0);
  useEffect(() => {
    const run = ++generation.current;
    const request = new AbortController();
    controller.current = request;
    setBusy('loading');
    setError('');
    void locationServiceSettingsActions
      .read(request.signal)
      .then((result) => {
        if (!request.signal.aborted && run === generation.current)
          setSettings(result);
      })
      .catch(() => {
        if (!request.signal.aborted && run === generation.current)
          setError('Не удалось загрузить настройки поиска адресов');
      })
      .finally(() => {
        if (!request.signal.aborted && run === generation.current)
          setBusy(undefined);
      });
    return () => {
      controller.current?.abort();
    };
  }, [retry]);

  const change = async (reset: boolean) => {
    if (busy || (!reset && !key.trim())) return;
    const run = ++generation.current;
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setBusy(reset ? 'resetting' : 'saving');
    setError('');
    setNotice('');
    try {
      const result = reset
        ? await locationServiceSettingsActions.reset(request.signal)
        : await locationServiceSettingsActions.save(
            { api_key: key.trim() },
            request.signal,
          );
      if (request.signal.aborted || run !== generation.current) return;
      setKey('');
      setSettings(result);
      setNotice(
        reset ? `Используется: ${sourceLabel[result.source]}` : 'Ключ сохранён',
      );
      onChanged(result);
    } catch {
      if (!request.signal.aborted && run === generation.current)
        setError(
          reset
            ? 'Не удалось переключить ключ. Повторите попытку'
            : 'Не удалось сохранить ключ. Повторите попытку',
        );
    } finally {
      if (!request.signal.aborted && run === generation.current)
        setBusy(undefined);
    }
  };

  return (
    <form
      id={id}
      aria-label="Ключ DaData"
      className="mt-3 grid gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        void change(false);
      }}
      onKeyDown={(event) => {
        if (event.key !== 'Escape') return;
        event.stopPropagation();
        event.preventDefault();
        if (!busy) onCancel();
      }}
    >
      {busy === 'loading' && <div role="status">Настройки поиска адресов</div>}
      {settings && (
        <>
          <div className="text-xs text-neutral-600">
            Сейчас используется: {sourceLabel[settings.source]}
          </div>
          <Field
            label="Личный API-ключ DaData"
            hint="Ключ сохранится на этом компьютере. Доступ к DaData проверяется при поиске адреса."
          >
            <TextInput
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={key}
              disabled={Boolean(busy)}
              onChange={(event) => {
                setKey(event.target.value);
                setNotice('');
              }}
            />
          </Field>
        </>
      )}
      {error && <div role="alert">{error}</div>}
      {notice && <div role="status">{notice}</div>}
      <div className="flex flex-wrap gap-2">
        {settings ? (
          <>
            <Button
              type="submit"
              variant="primary"
              loading={busy === 'saving'}
              disabled={Boolean(busy) || !key.trim()}
            >
              Сохранить ключ
            </Button>
            {settings.personal_key_configured &&
              settings.bundled_key_configured && (
                <Button
                  loading={busy === 'resetting'}
                  disabled={Boolean(busy)}
                  onClick={() => void change(true)}
                >
                  Использовать ключ приложения
                </Button>
              )}
          </>
        ) : (
          !busy && (
            <Button onClick={() => setRetry((value) => value + 1)}>
              Повторить загрузку настроек
            </Button>
          )
        )}
        <Button disabled={Boolean(busy)} onClick={onCancel}>
          Отмена
        </Button>
      </div>
    </form>
  );
}
