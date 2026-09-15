import type { AutonomousRunController } from '@/features/assistant/model/autonomous/useAutonomousRunController';
export interface PlacementResultProps extends Pick<
  AutonomousRunController,
  | 'result'
  | 'reviewStatus'
  | 'reviewNotice'
  | 'scopeLabels'
  | 'zones'
  | 'speciesIds'
  | 'speciesNames'
  | 'catalog'
  | 'arrangementLabel'
  | 'kinds'
  | 'incomplete'
  | 'run'
  | 'committed'
  | 'approval'
  | 'recoveryPending'
  | 'outcomeUnknown'
  | 'inputDisabled'
  | 'fillDraft'
  | 'answering'
> {}
