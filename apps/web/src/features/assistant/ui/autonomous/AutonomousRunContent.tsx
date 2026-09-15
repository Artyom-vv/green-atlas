import type { FC } from 'react';
import type { AutonomousPanelView } from './autonomousPanelView';
import {
  AutonomousZoneChange,
  CommittedZoneChange,
} from './AutonomousZoneChange';
import { ExistingResult } from './ExistingResult';
import { PlacementResult } from './PlacementResult';
import { ReadResults } from './ReadResults';
import { RunActivity } from './RunActivity';
import { RunFeedback } from './RunFeedback';
import { RunHistory } from './RunHistory';
import { RunQuestion } from './RunQuestion';
import { RunTask } from './RunTask';

type RunContentViews = AutonomousPanelView['content'];
export interface AutonomousRunContentProps extends RunContentViews {}

export const AutonomousRunContent: FC<AutonomousRunContentProps> = ({
  history,
  task,
  placement,
  existing,
  zonePreview,
  appliedZoneChange,
  read,
  question,
  activity,
  feedback,
}) => (
  <>
    <RunHistory {...history} />
    <RunTask {...task} />
    <PlacementResult {...placement} />
    <ExistingResult {...existing} />
    {zonePreview && <AutonomousZoneChange preview={zonePreview} />}
    {appliedZoneChange && <CommittedZoneChange change={appliedZoneChange} />}
    <ReadResults {...read} />
    <RunQuestion {...question} />
    <RunActivity {...activity} />
    <RunFeedback {...feedback} />
  </>
);
