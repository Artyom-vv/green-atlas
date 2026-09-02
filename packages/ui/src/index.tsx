import type {
  ButtonHTMLAttributes,
  CSSProperties,
  HTMLAttributes,
  InputHTMLAttributes,
  RefObject,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react';
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { LucideIcon } from 'lucide-react';
import { Check, ChevronDown, ExternalLink, Link2, LoaderCircle, Minus, Plus, X } from 'lucide-react';

export type Tone = 'neutral' | 'success' | 'warning' | 'error' | 'info';

const cx = (...values: Array<string | false | null | undefined>) => values.filter(Boolean).join(' ');

export function Text({ as: Tag = 'span', tone = 'default', mono = false, className, ...props }: HTMLAttributes<HTMLElement> & { as?: 'span' | 'p' | 'strong' | 'small' | 'h1' | 'h2' | 'h3'; tone?: 'default' | 'muted' | 'primary'; mono?: boolean }) {
  return <Tag className={cx('ui-text', `ui-text--${tone}`, mono && 'ui-text--mono', className)} {...props} />;
}

export function Icon({ icon: Component, size = 16, label }: { icon: LucideIcon; size?: number; label?: string }) {
  return <Component width={size} height={size} strokeWidth={1.5} aria-hidden={label ? undefined : true} aria-label={label} />;
}

export function Divider({ orientation = 'horizontal' }: { orientation?: 'horizontal' | 'vertical' }) {
  return <span className={cx('ui-divider', `ui-divider--${orientation}`)} aria-hidden="true" />;
}

export function Surface({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cx('ui-surface', className)} {...props} />;
}

export function Stack({ gap = 4, className, style, ...props }: HTMLAttributes<HTMLDivElement> & { gap?: number }) {
  return <div className={cx('ui-stack', className)} style={{ '--stack-gap': `var(--space-${gap})`, ...style } as CSSProperties} {...props} />;
}

export function Inline({ gap = 3, align = 'center', className, style, ...props }: HTMLAttributes<HTMLDivElement> & { gap?: number; align?: CSSProperties['alignItems'] }) {
  return <div className={cx('ui-inline', className)} style={{ '--inline-gap': `var(--space-${gap})`, alignItems: align, ...style } as CSSProperties} {...props} />;
}

export function Grid({ min = 220, gap = 4, className, style, ...props }: HTMLAttributes<HTMLDivElement> & { min?: number; gap?: number }) {
  return <div className={cx('ui-grid', className)} style={{ '--grid-min': `${min}px`, '--grid-gap': `var(--space-${gap})`, ...style } as CSSProperties} {...props} />;
}

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
export type ControlSize = 'compact' | 'default' | 'large';

const controlSizeClass = (size?: ControlSize) => size ? `ui-control--${size}` : undefined;

function TooltipBubble({ id, anchor, content, open, preferred = 'bottom' }: { id: string; anchor: RefObject<HTMLElement | null>; content: string; open: boolean; preferred?: 'top' | 'bottom' }) {
  const bubble = useRef<HTMLSpanElement>(null);
  const [position, setPosition] = useState<CSSProperties>();

  useLayoutEffect(() => {
    if (!open || !anchor.current || !bubble.current) return;
    const update = () => {
      if (!anchor.current || !bubble.current) return;
      const anchorRect = anchor.current.getBoundingClientRect();
      const bubbleRect = bubble.current.getBoundingClientRect();
      const gap = 8;
      const edge = 8;
      const canUsePreferred = preferred === 'bottom'
        ? anchorRect.bottom + gap + bubbleRect.height <= window.innerHeight - edge
        : anchorRect.top - gap - bubbleRect.height >= edge;
      const placement = canUsePreferred ? preferred : preferred === 'bottom' ? 'top' : 'bottom';
      const idealLeft = anchorRect.left + anchorRect.width / 2 - bubbleRect.width / 2;
      const left = Math.max(edge, Math.min(idealLeft, window.innerWidth - bubbleRect.width - edge));
      const top = placement === 'bottom' ? anchorRect.bottom + gap : anchorRect.top - bubbleRect.height - gap;
      const arrowLeft = Math.max(10, Math.min(anchorRect.left + anchorRect.width / 2 - left, bubbleRect.width - 10));
      bubble.current.dataset.placement = placement;
      setPosition({ left, top, '--tooltip-arrow-x': `${arrowLeft}px` } as CSSProperties);
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [anchor, open, preferred]);

  if (!open || typeof document === 'undefined') return null;
  return createPortal(<span ref={bubble} id={id} className="ui-tooltip-bubble" role="tooltip" style={position}>{content}</span>, document.body);
}

export function Button({ variant = 'secondary', loading = false, icon, children, className, disabled, controlSize, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; loading?: boolean; icon?: LucideIcon; controlSize?: ControlSize }) {
  return (
    <button className={cx('ui-button', `ui-button--${variant}`, controlSizeClass(controlSize), className)} data-size={controlSize ?? 'default'} disabled={disabled || loading} {...props}>
      {loading ? <LoaderCircle className="ui-spin" width={16} height={16} aria-hidden="true" /> : icon ? <Icon icon={icon} /> : null}
      <span>{children}</span>
    </button>
  );
}

export function IconButton({ icon, label, active = false, variant = 'secondary', controlSize, className, onMouseEnter, onMouseLeave, onFocus, onBlur, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { icon: LucideIcon; label: string; active?: boolean; variant?: ButtonVariant; controlSize?: ControlSize }) {
  const anchor = useRef<HTMLButtonElement>(null);
  const [tooltipOpen, setTooltipOpen] = useState(false);
  const tooltipId = useId();
  return (
    <>
      <button ref={anchor} className={cx('ui-icon-button', `ui-icon-button--${variant}`, active && 'is-active', controlSizeClass(controlSize), className)} aria-label={label} aria-describedby={tooltipOpen ? tooltipId : undefined} data-tooltip={label} data-size={controlSize ?? 'default'} onMouseEnter={(event) => { setTooltipOpen(true); onMouseEnter?.(event); }} onMouseLeave={(event) => { setTooltipOpen(false); onMouseLeave?.(event); }} onFocus={(event) => { setTooltipOpen(true); onFocus?.(event); }} onBlur={(event) => { setTooltipOpen(false); onBlur?.(event); }} {...props}>
        <Icon icon={icon} />
      </button>
      <TooltipBubble id={tooltipId} anchor={anchor} content={label} open={tooltipOpen} />
    </>
  );
}

export function FormField({ label, hint, error, required, children, className }: { label: string; hint?: string; error?: string; required?: boolean; children: ReactNode; className?: string }) {
  return (
    <label className={cx('ui-field', error && 'has-error', className)}>
      <span className="ui-field__label">{label}{required ? <span aria-hidden="true"> *</span> : null}</span>
      {children}
      {error ? <span className="ui-field__error" role="alert">{error}</span> : hint ? <span className="ui-field__hint">{hint}</span> : null}
    </label>
  );
}

export function TextInput({ className, controlSize, ...props }: Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> & { size?: number; controlSize?: ControlSize }) {
  return <input className={cx('ui-input', controlSizeClass(controlSize), className)} data-size={controlSize ?? 'default'} {...props} />;
}

export function NumberInput({ unit, className, controlSize, ...props }: Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> & { size?: number; unit?: string; controlSize?: ControlSize }) {
  return <span className="ui-unit-input"><input type="number" className={cx('ui-input', controlSizeClass(controlSize), className)} data-size={controlSize ?? 'default'} {...props} />{unit ? <span className="ui-unit-input__unit">{unit}</span> : null}</span>;
}

export function TextArea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cx('ui-textarea', className)} {...props} />;
}

export function Select({ className, children, controlSize, ...props }: SelectHTMLAttributes<HTMLSelectElement> & { controlSize?: ControlSize }) {
  return <span className={cx('ui-select-shell', controlSizeClass(controlSize))} data-size={controlSize ?? 'default'}>
    <select className={cx('ui-select', controlSizeClass(controlSize), className)} data-size={controlSize ?? 'default'} {...props}>{children}</select>
    <ChevronDown aria-hidden="true" />
  </span>;
}

export function Combobox({ value, options, placeholder = 'Выберите', emptyLabel = 'Ничего не найдено', disabled = false, controlSize, onChange, className }: {
  value?: string;
  options: Array<{ value: string; label: string; description?: string }>;
  placeholder?: string;
  emptyLabel?: string;
  disabled?: boolean;
  controlSize?: ControlSize;
  onChange: (value: string) => void;
  className?: string;
}) {
  const listboxId = useId();
  const selected = options.find((option) => option.value === value);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState(selected?.label ?? '');
  const [active, setActive] = useState(0);
  const normalized = query.trim().toLocaleLowerCase('ru');
  const filtered = options.filter((option) => !normalized || `${option.label} ${option.description ?? ''}`.toLocaleLowerCase('ru').includes(normalized));
  useEffect(() => { if (!open) setQuery(selected?.label ?? ''); }, [open, selected?.label]);
  const choose = (next: string) => { onChange(next); setOpen(false); };
  return <div className={cx('ui-combobox', controlSizeClass(controlSize), className)} data-size={controlSize ?? 'default'}>
    <input role="combobox" aria-expanded={open} aria-controls={listboxId} aria-autocomplete="list" disabled={disabled} value={query} placeholder={placeholder} onFocus={(event) => { setOpen(true); setActive(0); event.currentTarget.select(); }} onBlur={() => window.setTimeout(() => setOpen(false), 120)} onChange={(event) => { setQuery(event.target.value); setOpen(true); setActive(0); }} onKeyDown={(event) => {
      if (event.key === 'ArrowDown') { event.preventDefault(); setOpen(true); setActive((current) => Math.min(filtered.length - 1, current + 1)); }
      if (event.key === 'ArrowUp') { event.preventDefault(); setActive((current) => Math.max(0, current - 1)); }
      if (event.key === 'Enter' && open && filtered[active]) { event.preventDefault(); choose(filtered[active].value); }
      if (event.key === 'Escape') setOpen(false);
    }} />
    <ChevronDown aria-hidden="true" />
    {open ? <div id={listboxId} className="ui-combobox__list" role="listbox">{filtered.length ? filtered.map((option, index) => <button key={option.value} type="button" role="option" aria-selected={option.value === value} className={index === active ? 'is-active' : undefined} onMouseDown={(event) => event.preventDefault()} onClick={() => choose(option.value)}><span><strong>{option.label}</strong>{option.description ? <small>{option.description}</small> : null}</span>{option.value === value ? <Check aria-hidden="true" /> : null}</button>) : <span className="ui-combobox__empty">{emptyLabel}</span>}</div> : null}
  </div>;
}

export function Checkbox({ label, className, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode }) {
  return <label className={cx('ui-checkbox', className)}><input type="checkbox" {...props} /><span>{label}</span></label>;
}

export function StepProgress({ steps, current, label = 'Этапы настройки', className }: { steps: Array<{ id: string; label: string }>; current: number; label?: string; className?: string }) {
  const active = Math.max(0, Math.min(current, Math.max(0, steps.length - 1)));
  return <div className={cx('ui-step-progress', className)} aria-label={label}>
    <div className="ui-step-progress__track" aria-hidden="true">{steps.map((step, index) => <span key={step.id} className={index <= active ? 'is-complete' : undefined} />)}</div>
    <span className="ui-step-progress__label"><b>Шаг {active + 1} из {steps.length}</b><em>{steps[active]?.label}</em></span>
  </div>;
}

export function NumberStepper({ value, onChange, min = 0, max = Number.MAX_SAFE_INTEGER, step = 1, controlSize = 'default', label, disabled = false, className }: { value: number; onChange: (value: number) => void; min?: number; max?: number; step?: number; controlSize?: ControlSize; label: string; disabled?: boolean; className?: string }) {
  const [draft, setDraft] = useState(String(value));
  const clamp = (next: number) => Math.max(min, Math.min(max, Number(next.toFixed(6))));
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
  return <div className={cx('ui-number-stepper', controlSizeClass(controlSize), className)} data-size={controlSize} role="group" aria-label={label}>
    <button type="button" aria-label={`Уменьшить: ${label}`} disabled={disabled || value <= min} onClick={() => change(-step)}><Icon icon={Minus} /></button>
    <input type="number" inputMode="decimal" aria-label={label} disabled={disabled} min={min} max={max} step={step} value={draft} onFocus={(event) => event.currentTarget.select()} onChange={(event) => {
      const raw = event.target.value;
      setDraft(raw);
      if (raw === '' || raw === '-' || raw === '.' || raw === ',') return;
      const parsed = Number(raw.replace(',', '.'));
      if (Number.isFinite(parsed) && parsed >= min && parsed <= max) onChange(clamp(parsed));
    }} onBlur={() => commit()} onKeyDown={(event) => {
      if (event.key === 'Enter') event.currentTarget.blur();
      if (event.key === 'Escape') {
        setDraft(String(value));
        event.currentTarget.blur();
      }
    }} />
    <button type="button" aria-label={`Увеличить: ${label}`} disabled={disabled || value >= max} onClick={() => change(step)}><Icon icon={Plus} /></button>
  </div>;
}

export function ResourceLink({ title, meta, href, description, icon: ResourceIcon = Link2, className }: { title: ReactNode; meta?: ReactNode; href?: string; description?: ReactNode; icon?: LucideIcon; className?: string }) {
  const content = <>
    <span className="ui-resource-link__icon"><Icon icon={ResourceIcon} /></span>
    <span className="ui-resource-link__body"><strong>{title}</strong>{meta ? <small>{meta}</small> : null}{description ? <span>{description}</span> : null}</span>
    {href ? <span className="ui-resource-link__action"><Icon icon={ExternalLink} /></span> : null}
  </>;
  return href
    ? <a className={cx('ui-resource-link', className)} href={href} target="_blank" rel="noreferrer">{content}</a>
    : <div className={cx('ui-resource-link', className)}>{content}</div>;
}

export function Tooltip({ content, children }: { content: string; children: ReactNode }) {
  const anchor = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  const tooltipId = useId();
  return <><span ref={anchor} className="ui-tooltip" data-tooltip={content} aria-describedby={open ? tooltipId : undefined} tabIndex={0} onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}>{children}</span><TooltipBubble id={tooltipId} anchor={anchor} content={content} open={open} preferred="top" /></>;
}

export function Progress({ value, label }: { value?: number; label?: string }) {
  const normalized = value === undefined ? undefined : Math.max(0, Math.min(100, value));
  return <div className="ui-progress-wrap"><div className="ui-progress-meta"><span>{label}</span>{normalized !== undefined ? <span>{normalized}%</span> : null}</div><div className={cx('ui-progress', normalized === undefined && 'is-indeterminate')} role="progressbar" aria-label={label ?? 'Выполнение операции'} aria-valuemin={0} aria-valuemax={100} aria-valuenow={normalized}><span style={normalized === undefined ? undefined : { width: `${normalized}%` }} /></div></div>;
}

export function Panel({ title, description, actions, children, className }: { title?: ReactNode; description?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={cx('ui-panel', className)}>{title || description || actions ? <header className="ui-panel__header"><div>{title ? <h2>{title}</h2> : null}{description ? <p>{description}</p> : null}</div>{actions}</header> : null}<div className="ui-panel__content">{children}</div></section>;
}

export function Section({ title, children, className }: { title?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={cx('ui-section', className)}>{title ? <h3>{title}</h3> : null}{children}</section>;
}

export function Toolbar({ children, className, label = 'Панель инструментов' }: { children: ReactNode; className?: string; label?: string }) {
  return <div className={cx('ui-toolbar', className)} role="toolbar" aria-label={label} onKeyDown={(event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const controls = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
    const current = controls.indexOf(document.activeElement as HTMLButtonElement);
    if (!controls.length || current < 0) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? controls.length - 1 : event.key === 'ArrowRight' ? (current + 1) % controls.length : (current - 1 + controls.length) % controls.length;
    controls[next]?.focus();
  }}>{children}</div>;
}

export function ActionBar({ children, className }: { children: ReactNode; className?: string }) {
  return <footer className={cx('ui-action-bar', className)}>{children}</footer>;
}

export function StatusIndicator({ tone = 'neutral', label, value }: { tone?: Tone; label: ReactNode; value?: ReactNode }) {
  return <span className={cx('ui-status', `ui-status--${tone}`)}><span className="ui-status__mark" aria-hidden="true" /> <span>{label}</span>{value !== undefined ? <strong>{value}</strong> : null}</span>;
}

export function InlineMessage({ tone = 'info', title, children, onDismiss }: { tone?: Exclude<Tone, 'neutral'>; title?: ReactNode; children: ReactNode; onDismiss?: () => void }) {
  return <div className={cx('ui-message', `ui-message--${tone}`)} role={tone === 'error' ? 'alert' : 'status'}><div>{title ? <strong>{title}</strong> : null}<div>{children}</div></div>{onDismiss ? <IconButton icon={X} label="Закрыть" variant="ghost" onClick={onDismiss} /> : null}</div>;
}

export function EmptyState({ title, description, action }: { title: ReactNode; description?: ReactNode; action?: ReactNode }) {
  return <div className="ui-empty"><strong>{title}</strong>{description ? <p>{description}</p> : null}{action}</div>;
}

export function ListRow({ selected = false, leading, title, description, meta, actions, onClick }: { selected?: boolean; leading?: ReactNode; title: ReactNode; description?: ReactNode; meta?: ReactNode; actions?: ReactNode; onClick?: () => void }) {
  return <div className={cx('ui-list-row', selected && 'is-selected')} onClick={onClick} onKeyDown={onClick ? (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onClick(); } } : undefined} role={onClick ? 'button' : undefined} tabIndex={onClick ? 0 : undefined}>{leading}<div className="ui-list-row__body"><strong>{title}</strong>{description ? <span>{description}</span> : null}</div>{meta ? <div className="ui-list-row__meta">{meta}</div> : null}{actions}</div>;
}

export function DataTable({ children, className }: { children: ReactNode; className?: string }) {
  return <div className="ui-table-wrap"><table className={cx('ui-table', className)}>{children}</table></div>;
}

export function Dialog({ open, title, children, footer, onClose }: { open: boolean; title: ReactNode; children: ReactNode; footer?: ReactNode; onClose: () => void }) {
  const titleId = useId();
  const dialog = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusable = () => [...dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])') ?? []];
    requestAnimationFrame(() => (focusable()[0] ?? dialog.current)?.focus());
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); closeRef.current(); return; }
      if (event.key !== 'Tab') return;
      const controls = focusable();
      if (!controls.length) { event.preventDefault(); dialog.current?.focus(); return; }
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', handleKey);
    return () => { document.removeEventListener('keydown', handleKey); previousFocus?.focus(); };
  }, [open]);
  if (!open) return null;
  return <div className="ui-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><div ref={dialog} className="ui-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}><header><h2 id={titleId}>{title}</h2><IconButton icon={X} label="Закрыть" variant="ghost" onClick={onClose} /></header><div className="ui-dialog__content">{children}</div>{footer ? <footer>{footer}</footer> : null}</div></div>;
}
