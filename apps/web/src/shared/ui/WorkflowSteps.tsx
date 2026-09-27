import type { FC } from 'react';
import { StepProgress } from '@green/ui';

export interface WorkflowStepsProps {
  labels: string[];
  current: number;
  label: string;
}

export const WorkflowSteps: FC<WorkflowStepsProps> = ({
  labels,
  current,
  label,
}) => (
  <StepProgress
    steps={labels.map((item) => ({ id: item, label: item }))}
    current={current}
    label={label}
  />
);
