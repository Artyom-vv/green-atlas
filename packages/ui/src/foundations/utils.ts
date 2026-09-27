import { cn } from 'tailwind-variants';

/** Tailwind-aware merging keeps consumer overrides explicit and predictable. */
export const cx = (...values: Array<string | false | null | undefined>) =>
  cn(...values);

export const controlSizes = {
  compact:
    'min-h-(--control-height) [--control-height:var(--control-height-sm)] text-xs',
  default:
    'min-h-(--control-height) [--control-height:var(--control-height-default)] text-sm',
  large:
    'min-h-(--control-height) [--control-height:var(--control-height-lg)] text-sm',
} as const;

export type ControlSize = keyof typeof controlSizes;
