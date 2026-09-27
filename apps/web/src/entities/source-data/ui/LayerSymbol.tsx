import type { LayerKind } from '@green/api-client';
import { cx } from '@green/ui';
import type { FC } from 'react';

export interface LayerSymbolProps {
  color: string;
  kind?: LayerKind | null;
}
const mappedSymbols = {
  existing_green: 'rounded-full border-green-700 bg-green-200',
  utility: 'h-0.5 border-0 bg-warning-strong',
  water: 'border-blue-700 bg-blue-200',
} as const;
export const LayerSymbol: FC<LayerSymbolProps> = ({ color, kind }) => {
  const mapped =
    kind === 'existing_green' || kind === 'utility' || kind === 'water'
      ? mappedSymbols[kind]
      : undefined;
  return (
    <span
      aria-hidden="true"
      className={cx('inline-block size-3 shrink-0 border border-solid', mapped)}
      style={mapped ? undefined : { borderColor: color }}
    />
  );
};
