import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ComponentPropsWithRef,
  type FC,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';

export const WORKBENCH_SLOTS = [
  'resources',
  'resourcesNavigation',
  'inspector',
  'tool',
  'assistant',
  'checks',
  'schedule',
  'history',
] as const;
export type WorkbenchSlot = (typeof WORKBENCH_SLOTS)[number];

interface SlotRegistry {
  hosts: Partial<Record<WorkbenchSlot, HTMLDivElement>>;
  register: (slot: WorkbenchSlot, host: HTMLDivElement | null) => void;
}

const SlotContext = createContext<SlotRegistry | null>(null);

function useSlotRegistry() {
  const context = useContext(SlotContext);
  if (!context) throw new Error('WorkbenchSlotsProvider is required.');
  return context;
}

export interface WorkbenchSlotsProviderProps {
  children: ReactNode;
}

/** DOM targets belong to React layout context, never to persisted editor state. */
export const WorkbenchSlotsProvider: FC<WorkbenchSlotsProviderProps> = ({
  children,
}) => {
  const [hosts, setHosts] = useState<SlotRegistry['hosts']>({});
  const register = useCallback<SlotRegistry['register']>((slot, host) => {
    setHosts((current) => {
      if (current[slot] === (host ?? undefined)) return current;
      const next = { ...current };
      if (host) next[slot] = host;
      else delete next[slot];
      return next;
    });
  }, []);
  const registry = useMemo(() => ({ hosts, register }), [hosts, register]);
  return (
    <SlotContext.Provider value={registry}>{children}</SlotContext.Provider>
  );
};

export interface WorkbenchHostProps extends Omit<
  ComponentPropsWithRef<'div'>,
  'ref' | 'children'
> {
  slot: WorkbenchSlot;
}

/** Host identity stays stable across tabs and resize. Hidden hosts stay mounted. */
export const WorkbenchHost: FC<WorkbenchHostProps> = ({ slot, ...props }) => {
  const { register } = useSlotRegistry();
  const ref = useCallback(
    (node: HTMLDivElement | null) => register(slot, node),
    [register, slot],
  );
  return <div {...props} data-workbench-slot={slot} ref={ref} />;
};

export interface WorkbenchContributionProps {
  slot: WorkbenchSlot;
  children: ReactNode;
  /** Standalone consumers can render the same content without an IDE host. */
  fallback?: ReactNode;
}

/** Portals preserve the originating form/providers while the layout owns space. */
export const WorkbenchContribution: FC<WorkbenchContributionProps> = ({
  slot,
  children,
  fallback,
}) => {
  const registry = useContext(SlotContext);
  if (!registry) {
    if (fallback === undefined)
      throw new Error('WorkbenchSlotsProvider is required.');
    return fallback;
  }
  const target = registry.hosts[slot];
  return target ? createPortal(children, target) : null;
};
