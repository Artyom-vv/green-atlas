import type { ReactNode } from 'react';
import { Toolbar } from '@green/ui';

/** Shared chrome for map controls; behavior stays with the controls themselves. */
export function MapControlGroup({ children, orientation = 'horizontal', label = 'Управление картой', className = '' }: { children: ReactNode; orientation?: 'horizontal' | 'vertical'; label?: string; className?: string }) {
  return <Toolbar className={`map-control-group map-control-group--${orientation} ${className}`.trim()} label={label}>{children}</Toolbar>;
}
