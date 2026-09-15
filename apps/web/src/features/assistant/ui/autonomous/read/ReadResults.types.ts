import type { AutonomousRunController } from '@/features/assistant/model/autonomous/useAutonomousRunController';
export interface ReadResultsProps extends Pick<
  AutonomousRunController,
  'shortlist' | 'project' | 'issues'
> {}
