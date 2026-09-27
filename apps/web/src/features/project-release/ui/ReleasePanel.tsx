import type { FC } from 'react';
import { FormProvider, type UseFormReturn } from 'react-hook-form';
import type {
  Plan,
  ReleaseCreateRequest,
  ReleasePackage,
} from '@green/api-client';
import { ControlProvider, InlineMessage } from '@green/ui';
import type { GrowthHorizon } from '@/entities/planting-forecast/ui/GrowthHorizonControl';
import type { ReleaseFormValues } from '../model/releaseForm';
import type { ReleaseDraftContext } from '../model/releaseDraft';
import { ReleaseForm } from './ReleaseForm';
import { ReleaseFiles } from './ReleaseFiles';
import { ReleaseDraftStatus } from './ReleaseDraftStatus';
import { ReleaseHeader } from './ReleaseHeader';

export interface ReleasePanelProps {
  plan: Plan;
  geometryVersion?: number;
  release?: ReleasePackage;
  formMethods: UseFormReturn<ReleaseFormValues>;
  formOpen: boolean;
  hasDraft?: boolean;
  storageAvailable?: boolean;
  draftStale?: boolean;
  draftContext?: Pick<ReleaseDraftContext, 'planVersion' | 'geometryVersion'>;
  draftNotice?: string;
  submissionUnknown?: boolean;
  onOpenForm: () => void;
  onShowFiles: () => void;
  onClearDraft: () => void;
  onReviewContext: () => void;
  onGrowthHorizon?: (value: GrowthHorizon) => void;
  loading?: boolean;
  error?: string;
  onCreate: (request: ReleaseCreateRequest) => void;
  onDownload: (path: string) => void;
}

export const ReleasePanel: FC<ReleasePanelProps> = ({
  plan,
  geometryVersion,
  release,
  formMethods,
  formOpen,
  hasDraft,
  storageAvailable = true,
  draftStale,
  draftContext,
  draftNotice,
  submissionUnknown,
  onOpenForm,
  onShowFiles,
  onClearDraft,
  onReviewContext,
  onGrowthHorizon,
  loading,
  error,
  onCreate,
  onDownload,
}) => {
  const showRelease = Boolean(release && !formOpen);
  return (
    <ControlProvider size="compact">
      <FormProvider {...formMethods}>
        <div className="@container/release grid min-w-0 gap-4 text-xs leading-[18px] text-neutral-800">
          <ReleaseHeader
            planVersion={plan.version}
            showRelease={showRelease}
            hasFiles={Boolean(release)}
            loading={loading}
            onShowFiles={onShowFiles}
          />
          {showRelease && release ? (
            <ReleaseFiles
              release={release}
              plan={plan}
              geometryVersion={geometryVersion}
              hasDraft={hasDraft}
              loading={loading}
              onDownload={onDownload}
              onOpenForm={onOpenForm}
            />
          ) : (
            <>
              <ReleaseDraftStatus
                hasDraft={hasDraft}
                storageAvailable={storageAvailable}
                draftStale={draftStale}
                draftContext={draftContext}
                planVersion={plan.version}
                geometryVersion={geometryVersion}
                draftNotice={draftNotice}
                submissionUnknown={submissionUnknown}
                loading={loading}
                onClearDraft={onClearDraft}
                onReviewContext={onReviewContext}
              />
              <ReleaseForm
                plan={plan}
                draftStale={draftStale}
                loading={loading}
                onCreate={onCreate}
                onGrowthHorizon={onGrowthHorizon}
              />
            </>
          )}
          {Boolean(error) && (
            <InlineMessage tone="error">{error}</InlineMessage>
          )}
          <p className="m-0 text-xs text-neutral-600">
            Пакет не является согласованием или порубочным билетом.
          </p>
        </div>
      </FormProvider>
    </ControlProvider>
  );
};
