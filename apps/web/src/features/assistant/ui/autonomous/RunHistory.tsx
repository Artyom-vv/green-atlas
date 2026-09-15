import {
  capacity,
  errorText,
  placementData,
  runStatus,
} from '@/features/assistant/model/autonomous/presentation';
import type { AutonomousRunController } from '@/features/assistant/model/autonomous/useAutonomousRunController';
import { type AutonomousRunOptions } from '@/features/assistant/model/autonomous/useAutonomousRunController';
import { AssistantRecordButton } from '@/features/assistant/ui/shared/AssistantRecordButton';
import { Disclosure, Text } from '@green/ui';
import type { FC } from 'react';
export interface RunHistoryProps
  extends
    Pick<
      AutonomousRunController,
      | 'historyOpen'
      | 'setHistoryOpen'
      | 'runs'
      | 'runId'
      | 'decisionDisabled'
      | 'openRun'
    >,
    Pick<AutonomousRunOptions, 'projectId'> {}
export const RunHistory: FC<RunHistoryProps> = ({
  historyOpen,
  setHistoryOpen,
  runs,
  runId,
  decisionDisabled,
  openRun,
  projectId,
}) => (
  <>
    <Disclosure
      variant="plain"
      open={historyOpen}
      onOpenChange={setHistoryOpen}
      title={
        <>
          <span>История запусков</span>
        </>
      }
    >
      {runs.isPending ? (
        <Text role="status" as="p" variant="body" className="m-0 wrap-anywhere">
          Загружаю запуски…
        </Text>
      ) : runs.isError ? (
        <Text role="alert" as="p" variant="body" className="m-0 wrap-anywhere">
          {errorText(runs.error)}
        </Text>
      ) : (
        <div>
          {runs.data
            ?.filter((item) => item.state.project_id === projectId)
            .map((item) => (
              <AssistantRecordButton
                type="button"
                key={item.state.run_id}
                aria-current={item.state.run_id === runId ? 'true' : undefined}
                disabled={decisionDisabled}
                onClick={() => void openRun(item)}
                title={
                  <>{String(item.state.intent.raw_text ?? 'Задача агента')}</>
                }
                description={
                  <>
                    {runStatus(item, capacity(placementData(item)))}
                    {Number.isFinite(Date.parse(item.created_at))
                      ? ` · ${new Date(item.created_at).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })}`
                      : ''}
                  </>
                }
              />
            ))}
          {runs.data?.length === 0 && (
            <Text as="p" variant="body" className="m-0 wrap-anywhere">
              Запусков пока нет.
            </Text>
          )}
        </div>
      )}
    </Disclosure>
  </>
);
