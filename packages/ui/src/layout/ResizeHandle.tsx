import { useRef, type FC, type PointerEvent } from 'react';
import { resizeHandle } from './resizeHandleVariants';

const DEFAULT_RESIZE_STEP = 16;
interface DragOrigin {
  coordinate: number;
  value: number;
}
export interface ResizeHandleProps {
  label: string;
  orientation: 'horizontal' | 'vertical';
  value: number;
  min: number;
  max: number;
  reverse?: boolean;
  step?: number;
  onChange: (value: number) => void;
  onReset: () => void;
  className?: string;
}

export const ResizeHandle: FC<ResizeHandleProps> = ({
  label,
  orientation,
  value,
  min,
  max,
  reverse = false,
  step = DEFAULT_RESIZE_STEP,
  onChange,
  onReset,
  className,
}) => {
  const drag = useRef<DragOrigin | undefined>(undefined);
  const coordinate = (event: PointerEvent) =>
    orientation === 'vertical' ? event.clientX : event.clientY;
  const update = (next: number) =>
    onChange(Math.round(Math.max(min, Math.min(max, next))));
  const styles = resizeHandle({ orientation });
  return (
    <div
      role="separator"
      tabIndex={0}
      aria-label={label}
      aria-orientation={orientation}
      aria-valuenow={Math.round(value)}
      aria-valuemin={min}
      aria-valuemax={max}
      className={styles.root({ className })}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        drag.current = { coordinate: coordinate(event), value };
        event.currentTarget.setPointerCapture(event.pointerId);
        event.currentTarget.focus();
        event.preventDefault();
      }}
      onPointerMove={(event) => {
        if (drag.current)
          update(
            drag.current.value +
              (coordinate(event) - drag.current.coordinate) *
                (reverse ? -1 : 1),
          );
      }}
      onPointerUp={(event) => {
        drag.current = undefined;
        if (event.currentTarget.hasPointerCapture(event.pointerId))
          event.currentTarget.releasePointerCapture(event.pointerId);
      }}
      onPointerCancel={() => {
        drag.current = undefined;
      }}
      onLostPointerCapture={() => {
        drag.current = undefined;
      }}
      onDoubleClick={onReset}
      onKeyDown={(event) => {
        const positive =
          orientation === 'vertical' ? 'ArrowRight' : 'ArrowDown';
        const negative = orientation === 'vertical' ? 'ArrowLeft' : 'ArrowUp';
        if (event.key === positive || event.key === negative) {
          event.preventDefault();
          update(
            value +
              (event.key === positive ? step : -step) * (reverse ? -1 : 1),
          );
        }
        if (event.key === 'Home' || event.key === 'End') {
          event.preventDefault();
          update(event.key === 'Home' ? min : max);
        }
      }}
    >
      <span className={styles.grip()} aria-hidden="true" />
    </div>
  );
};
