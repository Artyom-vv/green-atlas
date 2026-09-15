import type { FC } from 'react';
import type { AutonomousPanelView } from './autonomousPanelView';
import { RunApproval } from './RunApproval';
import { RunComposer } from './RunComposer';

type RunActionViews = AutonomousPanelView['actions'];
export interface AutonomousRunActionsProps extends RunActionViews {}

export const AutonomousRunActions: FC<AutonomousRunActionsProps> = ({
  approval,
  composer,
}) => (
  <>
    <RunApproval {...approval} />
    <RunComposer {...composer} />
  </>
);
