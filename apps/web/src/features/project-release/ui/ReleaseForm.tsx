import { useId, type FC } from 'react';
import { useFormContext, useWatch } from 'react-hook-form';
import type { Plan, ReleaseCreateRequest } from '@green/api-client';
import { FileArchive, PackageCheck } from 'lucide-react';
import { Button, FieldGroup, FormActions, StatusIndicator } from '@green/ui';
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
  loading?: boolean;
  onCreate: (request: ReleaseCreateRequest) => void;
  onGrowthHorizon?: (value: GrowthHorizon) => void;
}

export const ReleaseForm: FC<ReleaseFormProps> = ({
  plan,
  draftStale,
  loading,
  onCreate,
  onGrowthHorizon,
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
          className="rounded-control grid gap-2 bg-neutral-100 p-3"
          aria-label="Проверка плана перед выпуском"
        >
          {sourcePending && (
            <StatusIndicator
              tone="warning"
              label="Ограничения исходных данных"
              value="Не проверены"
            />
          )}
          <StatusIndicator
            tone={hardErrors ? 'error' : 'success'}
            label="Ошибки размещения"
            value={hardErrors || 'Нет'}
          />
          <StatusIndicator
            tone={missingSpecies ? 'warning' : 'success'}
            label="Без назначенного вида"
            value={missingSpecies || 'Нет'}
          />
        </section>
        {mode === 'final' && <ReleaseBasisFields />}
      </FieldGroup>
      <div className="grid gap-2 border-0 border-t border-solid border-neutral-200 pt-3">
        {blockedReasons.length > 0 && (
          <div id={reasonId} className="grid gap-1 text-xs text-neutral-600">
            {blockedReasons.map((reason) => (
              <p className="m-0" key={reason}>
                {reason}
              </p>
            ))}
          </div>
        )}
        <FormActions className="justify-start">
          <Button
            variant="primary"
            startIcon={mode === 'draft' ? <FileArchive /> : <PackageCheck />}
            loading={loading}
            disabled={!ready}
            aria-describedby={blockedReasons.length ? reasonId : undefined}
            onClick={() => onCreate(releaseDraftRequest(getValues()))}
          >
            {mode === 'draft'
              ? 'Собрать черновой пакет'
              : 'Собрать финальный пакет'}
          </Button>
        </FormActions>
      </div>
    </>
  );
};
