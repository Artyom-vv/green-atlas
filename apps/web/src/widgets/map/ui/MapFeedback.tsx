import type { MoveLiveValidation } from '@/features/plan-changes/model/moveLiveValidation';
import type { PlacementCheck } from '@green/api-client';
import { tv } from '@green/ui';
import type { FC, ReactNode } from 'react';

const statusColors = {
  allowed: 'text-green-700',
  blocked: 'text-error',
  unknown: 'text-neutral-600',
  checking: 'text-blue-700',
  soft_conflict: 'text-warning-strong',
} satisfies Record<
  PlacementCheck['status'] | MoveLiveValidation['status'],
  string
>;
const feedback = tv({
  base: 'rounded-control max-w-80 bg-white px-3 py-2 text-xs break-words shadow-sm',
  variants: { status: statusColors },
  defaultVariants: { status: 'unknown' },
});

export interface MapFeedbackProps {
  status?: keyof typeof statusColors;
  children: ReactNode;
}
export const MapFeedback: FC<MapFeedbackProps> = ({
  status = 'unknown',
  children,
}) => (
  <span role="status" className={feedback({ status })}>
    {children}
  </span>
);
