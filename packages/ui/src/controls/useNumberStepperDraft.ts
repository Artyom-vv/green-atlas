import { useEffect, useRef, useState, type InputHTMLAttributes } from 'react';
interface NumberStepperDraftOptions {
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}
const DECIMAL_PRECISION = 6;
export function useNumberStepperDraft({
  value,
  min,
  max,
  onChange,
}: NumberStepperDraftOptions) {
  const [draft, setDraft] = useState(String(value));
  const discardOnBlur = useRef(false);
  const clamp = (next: number) =>
    Math.max(min, Math.min(max, Number(next.toFixed(DECIMAL_PRECISION))));
  useEffect(() => setDraft(String(value)), [value]);
  const commit = (raw = draft) => {
    const parsed = Number(raw.replace(',', '.'));
    const next = Number.isFinite(parsed) ? clamp(parsed) : value;
    setDraft(String(next));
    if (next !== value) onChange(next);
  };
  const change = (delta: number) => {
    const next = clamp(value + delta);
    setDraft(String(next));
    onChange(next);
  };
  const inputProps: Pick<
    InputHTMLAttributes<HTMLInputElement>,
    'value' | 'onChange' | 'onBlur' | 'onKeyDown'
  > = {
    value: draft,
    onChange: (event) => {
      const raw = event.target.value;
      setDraft(raw);
      if (raw === '' || raw === '-' || raw === '.' || raw === ',') return;
      const parsed = Number(raw.replace(',', '.'));
      if (Number.isFinite(parsed) && parsed >= min && parsed <= max)
        onChange(clamp(parsed));
    },
    onBlur: () => {
      if (discardOnBlur.current) {
        discardOnBlur.current = false;
        return;
      }
      commit();
    },
    onKeyDown: (event) => {
      if (event.key === 'Enter') event.currentTarget.blur();
      if (event.key === 'Escape') {
        discardOnBlur.current = true;
        setDraft(String(value));
        event.currentTarget.blur();
      }
    },
  };
  return { inputProps, change };
}
