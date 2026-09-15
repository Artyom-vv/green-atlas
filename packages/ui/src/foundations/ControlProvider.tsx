import { createContext, useContext, type FC, type ReactNode } from 'react';
import type { ControlSize } from './utils';

const ControlSizeContext = createContext<ControlSize>('default');

export interface ControlProviderProps {
  size: ControlSize;
  children: ReactNode;
}

/** A layout supplies density without selecting or restyling nested DOM. */
export const ControlProvider: FC<ControlProviderProps> = ({
  size,
  children,
}) => <ControlSizeContext value={size}>{children}</ControlSizeContext>;

export const useControlSize = (size?: ControlSize): ControlSize => {
  const inheritedSize = useContext(ControlSizeContext);
  return size ?? inheritedSize;
};
