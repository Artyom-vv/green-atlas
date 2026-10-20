import type { LocationSuggestion } from '@green/api-client';
import { Autocomplete, Field } from '@green/ui';
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import {
  locationLabel,
  locationDescription,
} from '../model/locationPresentation';
import { locationActions } from '../model/useProjectLocation';

export function LocationSearchField({
  projectId,
  disabled,
  value,
  onChange,
  onSelect,
  configurationRevision = 0,
}: {
  projectId: string;
  disabled?: boolean;
  value: string;
  onChange: (value: string) => void;
  onSelect: (value: LocationSuggestion) => void;
  configurationRevision?: number;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState<LocationSuggestion[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const generation = useRef(0);
  const visible = open && !disabled && value.trim().length >= 2;
  useEffect(() => {
    const run = ++generation.current;
    const controller = new AbortController();
    setResults([]);
    setError('');
    setLoading(visible);
    if (!visible) return () => controller.abort();
    const timer = window.setTimeout(() => {
      void locationActions
        .suggest(
          projectId,
          { query: value.trim(), kind: 'street' },
          controller.signal,
        )
        .then((result) => {
          if (controller.signal.aborted || run !== generation.current) return;
          setResults(result.suggestions);
          if (!result.configured)
            setError(
              result.message?.replace(/[.!]$/, '') ||
                'Поиск адресов не настроен',
            );
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted || run !== generation.current) return;
          setError(
            error instanceof Error
              ? error.message.replace(/[.!]$/, '')
              : 'Поиск адресов недоступен',
          );
        })
        .finally(() => {
          if (!controller.signal.aborted && run === generation.current)
            setLoading(false);
        });
    }, 300);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [projectId, visible, value, retry, configurationRevision]);
  const options = useMemo(
    () =>
      results.map((item) => ({
        value: item.id,
        label: locationLabel(item),
        description: locationDescription(item),
      })),
    [results],
  );
  return (
    <Field label="Адрес" htmlFor={id}>
      <Autocomplete
        id={id}
        value={value}
        options={options}
        open={visible}
        disabled={disabled}
        loading={loading}
        error={error}
        placeholder="Город, улица"
        onOpenChange={setOpen}
        onChange={onChange}
        onRetry={() => setRetry((n) => n + 1)}
        onSelect={(option) => {
          const selected = results.find((item) => item.id === option.value);
          if (selected) {
            generation.current++;
            setOpen(false);
            onSelect(selected);
          }
        }}
      />
    </Field>
  );
}
