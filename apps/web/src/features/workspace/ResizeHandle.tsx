import { useRef, type PointerEvent } from 'react';

/** Pointer capture keeps dragging local; arrow keys provide the same control. */
export function ResizeHandle({ label, orientation, value, min, max, reverse = false, onChange, onReset, className = '' }: {
  label: string; orientation: 'horizontal' | 'vertical'; value: number; min: number; max: number;
  reverse?: boolean; onChange: (value: number) => void; onReset: () => void; className?: string;
}) {
  const drag = useRef<{ coordinate: number; value: number } | undefined>(undefined);
  const coordinate = (event: PointerEvent) => orientation === 'vertical' ? event.clientX : event.clientY;
  const update = (next: number) => onChange(Math.round(Math.max(min, Math.min(max, next))));
  return <div role="separator" tabIndex={0} aria-label={label} aria-orientation={orientation} aria-valuenow={Math.round(value)} aria-valuemin={min} aria-valuemax={max}
    className={`editor-resize editor-resize--${orientation} ${className}`}
    onPointerDown={event => { if (event.button !== 0) return; drag.current = { coordinate: coordinate(event), value }; event.currentTarget.setPointerCapture(event.pointerId); event.preventDefault(); }}
    onPointerMove={event => { if (drag.current) update(drag.current.value + (coordinate(event) - drag.current.coordinate) * (reverse ? -1 : 1)); }}
    onPointerUp={event => { drag.current = undefined; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
    onLostPointerCapture={() => { drag.current = undefined; }} onDoubleClick={onReset}
    onKeyDown={event => {
      const positive = orientation === 'vertical' ? 'ArrowRight' : 'ArrowDown';
      const negative = orientation === 'vertical' ? 'ArrowLeft' : 'ArrowUp';
      if (event.key === positive || event.key === negative) { event.preventDefault(); update(value + (event.key === positive ? 16 : -16) * (reverse ? -1 : 1)); }
      if (event.key === 'Home' || event.key === 'End') { event.preventDefault(); update(event.key === 'Home' ? min : max); }
    }}><span /></div>;
}
