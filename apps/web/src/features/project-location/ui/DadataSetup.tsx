import { Button, TextInput } from '@green/ui';
import { useEffect, useState } from 'react';

/** Only the authenticated desktop session serves this endpoint. */
export function DadataSetup({ onSaved }: { onSaved: () => void }) {
  const [available, setAvailable] = useState(false);
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    void fetch('/_desktop/location/dadata', {
      credentials: 'same-origin',
      signal: controller.signal,
    })
      .then((response) => {
        if (response.ok) setAvailable(true);
      })
      .catch(() => {});
    return () => controller.abort();
  }, []);
  if (!available) return null;
  const save = async () => {
    setBusy(true);
    setError('');
    try {
      const response = await fetch('/_desktop/location/dadata', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: key.trim() }),
      });
      if (!response.ok) throw new Error('Не удалось сохранить ключ');
      setKey('');
      setOpen(false);
      onSaved();
    } catch {
      setError('Не удалось сохранить ключ. Проверьте его и повторите.');
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mt-3 rounded-lg border border-neutral-200 bg-neutral-50 p-3 text-sm">
      <p>
        Для поиска адреса нужен личный ключ DaData. OSM загрузится после выбора
        улицы.
      </p>
      {!open ? (
        <Button className="mt-2" variant="ghost" onClick={() => setOpen(true)}>
          Настроить DaData
        </Button>
      ) : (
        <div className="mt-3 flex flex-col gap-2">
          <label htmlFor="dadata-api-key">API-ключ DaData</label>
          <TextInput
            id="dadata-api-key"
            type="password"
            autoComplete="off"
            value={key}
            onChange={(event) => setKey(event.target.value)}
            disabled={busy}
          />
          {error && (
            <p role="alert" className="text-red-700">
              {error}
            </p>
          )}
          <Button
            disabled={busy || key.trim().length < 8}
            onClick={() => void save()}
          >
            Сохранить ключ локально
          </Button>
        </div>
      )}
    </div>
  );
}
