import { useId, type FC, type ReactNode } from 'react';
import { useFormContext, useWatch } from 'react-hook-form';
import type { Plan, ReleaseCreateRequest } from '@green/api-client';
import { FileArchive, PackageCheck } from 'lucide-react';
import { Button, FieldGroup, FormActions, InlineMessage } from '@green/ui';
import type { GrowthHorizon } from '@/entities/planting-forecast/ui/GrowthHorizonControl';
import { releaseDraftRequest } from '../model/releaseDraft';
import type { ReleaseFormValues } from '../model/releaseForm';
import { releaseReadiness } from '../model/releaseReadiness';
import { ReleaseBasisFields } from './ReleaseBasisFields';
import { ReleaseModeFields } from './ReleaseModeFields';
import { ReleaseForecastField } from './ReleaseForecastField';

export interface ReleaseFormProps {
  plan: Plan;
  draftStale?: boolean;
  submissionUnknown?: boolean;
  loading?: boolean;
  onCreate: (request: ReleaseCreateRequest) => void;
  onGrowthHorizon?: (value: GrowthHorizon) => void;
  footerAside?: ReactNode;
}

export const ReleaseForm: FC<ReleaseFormProps> = ({
  plan,
  draftStale,
  submissionUnknown,
  loading,
  onCreate,
  onGrowthHorizon,
  footerAside,
}) => {
  const { control, getValues } = useFormContext<ReleaseFormValues>();
  const mode = useWatch({ control, name: 'mode' });
  const basis = useWatch({ control, name: 'basis' });
  const sceneHorizon = useWatch({ control, name: 'sceneHorizon' });
  const { missingSpecies, hardErrors, sourcePending, blockedReasons, ready } =
    releaseReadiness(plan, { mode, basis, sceneHorizon }, draftStale);
  const reasonId = useId();
  return (
    <>
      <FieldGroup disabled={loading} aria-label="Параметры выпуска">
        <ReleaseModeFields disabled={loading} />
        {onGrowthHorizon && (
          <ReleaseForecastField onGrowthHorizon={onGrowthHorizon} />
        )}
        <section
          className="grid gap-2 border-0 border-t border-solid border-neutral-200 pt-3"
          aria-label="Проверка плана перед выпуском"
        >
          {sourcePending && (
            <InlineMessage tone="warning">
              <span>Ограничения исходных данных</span> <span>Не проверены</span>
            </InlineMessage>
          )}
          <dl className="m-0 grid grid-cols-2 gap-4 text-xs [&_dd]:m-0 [&>div]:flex [&>div]:items-baseline [&>div]:justify-between [&>div]:gap-2">
            <div>
              <dt className="text-neutral-600">Ошибки размещения</dt>
              <dd className={hardErrors ? 'text-error' : ''}>
                {hardErrors || 'Нет'}
              </dd>
            </div>
            <div>
              <dt className="text-neutral-600">Без назначенного вида</dt>
              <dd className={missingSpecies ? 'text-warning-strong' : ''}>
                {missingSpecies || 'Нет'}
              </dd>
            </div>
          </dl>
        </section>
        {mode === 'final' && <ReleaseBasisFields />}
      </FieldGroup>
      <div className="grid gap-2">
        {blockedReasons.length > 0 && (
          <div id={reasonId} className="grid gap-1 text-xs text-neutral-600">
            {blockedReasons.map((reason) => (
              <p className="m-0" key={reason}>
                {reason}
              </p>
            ))}
          </div>
        )}
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-neutral-200 pt-3">
          <div>{footerAside}</div>
          <FormActions>
            <Button
              variant="primary"
              startIcon={mode === 'draft' ? <FileArchive /> : <PackageCheck />}
              loading={loading}
              loadingIndicator={false}
              disabled={!ready || submissionUnknown}
              aria-describedby={blockedReasons.length ? reasonId : undefined}
              onClick={() => onCreate(releaseDraftRequest(getValues()))}
              aria-label={
                mode === 'draft'
                  ? 'Собрать черновой пакет'
                  : 'Собрать финальный пакет'
              }
            >
              Собрать пакет
            </Button>
          </FormActions>
        </div>
      </div>
    </>
  );
};
