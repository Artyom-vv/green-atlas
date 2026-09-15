import { useId, useState, type ComponentPropsWithRef } from 'react';

interface IconTooltipOptions extends Pick<
  ComponentPropsWithRef<'button'>,
  | 'onMouseEnter'
  | 'onMouseLeave'
  | 'onFocus'
  | 'onBlur'
  | 'aria-expanded'
  | 'aria-describedby'
> {
  disabled: boolean;
}

export function useIconTooltip(options: IconTooltipOptions) {
  const [requested, setRequested] = useState(false);
  const id = useId();
  const expanded = options['aria-expanded'];
  const open =
    requested && !options.disabled && expanded !== true && expanded !== 'true';
  const triggerProps: Pick<
    ComponentPropsWithRef<'button'>,
    'onMouseEnter' | 'onMouseLeave' | 'onFocus' | 'onBlur' | 'aria-describedby'
  > = {
    'aria-describedby':
      [options['aria-describedby'], open ? id : undefined]
        .filter(Boolean)
        .join(' ') || undefined,
    onMouseEnter: (event) => {
      setRequested(true);
      options.onMouseEnter?.(event);
    },
    onMouseLeave: (event) => {
      setRequested(false);
      options.onMouseLeave?.(event);
    },
    onFocus: (event) => {
      setRequested(true);
      options.onFocus?.(event);
    },
    onBlur: (event) => {
      setRequested(false);
      options.onBlur?.(event);
    },
  };
  return { id, open, triggerProps };
}
