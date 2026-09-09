import { useEffect, useState, type ReactNode } from 'react';
import { ChevronRight, PanelRightClose } from 'lucide-react';
import { IconButton } from '@green/ui';
import './editor-panel.css';

/** The inspector's own composition, independent of retired card layouts. */
export function EditorPanel({ title, children, onClose, label }: { title: ReactNode; children: ReactNode; onClose?: () => void; label?: string }) {
  return <section className="editor-panel" aria-label={label}>
    <header className="editor-panel__header"><h2>{title}</h2>{onClose ? <IconButton icon={PanelRightClose} variant="ghost" controlSize="compact" label="Скрыть боковую панель" onClick={onClose} /> : null}</header>
    <div className="editor-panel__content">{children}</div>
  </section>;
}

export function EditorField({ label, children }: { label: ReactNode; children: ReactNode }) {
  return <div className="editor-field"><span>{label}</span><div>{children}</div></div>;
}

export function EditorNumber({ label, value, onChange, min, max, step = 1, disabled }: { label: string; value: number; onChange: (value: number) => void; min: number; max: number; step?: number; disabled?: boolean }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  return <input className="editor-number" type="number" aria-label={label} value={text} min={min} max={max} step={step} disabled={disabled}
    onChange={event => { const next = event.target.value; setText(next); const number = Number(next); if (next && Number.isFinite(number) && number >= min && number <= max) onChange(number); }}
    onBlur={() => { const number = text ? Number(text) : value; const next = Math.max(min, Math.min(max, Number.isFinite(number) ? number : value)); setText(String(next)); if (next !== value) onChange(next); }} />;
}

export function EditorDisclosure({ title, children, defaultOpen = false }: { title: ReactNode; children: ReactNode; defaultOpen?: boolean }) {
  const [expanded, setExpanded] = useState(defaultOpen);
  return <details className="editor-disclosure" open={expanded} onToggle={event => setExpanded(event.currentTarget.open)}><summary><ChevronRight size={14} />{title}</summary><div>{children}</div></details>;
}

export function EditorActions({ children, grid = false }: { children: ReactNode; grid?: boolean }) {
  return <div className={`editor-actions${grid ? ' editor-actions--grid' : ''}`}>{children}</div>;
}
