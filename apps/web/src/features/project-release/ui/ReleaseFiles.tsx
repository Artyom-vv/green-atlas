import type { FC } from 'react';
import type { Plan, ReleasePackage } from '@green/api-client';
import { Button, Disclosure, InlineMessage, StatusIndicator } from '@green/ui';
import { horizonLabel } from '../model/releaseForm';
import { ReleaseDownloads } from './ReleaseDownloads';

export interface ReleaseFilesProps {
  release: ReleasePackage;
  plan: Plan;
  geometryVersion?: number;
  hasDraft?: boolean;
  loading?: boolean;
  onDownload: (path: string) => void;
  onOpenForm: () => void;
}

export const ReleaseFiles: FC<ReleaseFilesProps> = ({
  release,
  plan,
  geometryVersion,
  hasDraft,
  loading,
  onDownload,
  onOpenForm,
}) => {
  return (
    <>
      <section
        className="rounded-control grid gap-2 border border-solid border-neutral-200 p-3"
        aria-label="Сохранённый пакет"
      >
        <StatusIndicator
          tone="success"
          label={
            release.mode === 'draft'
              ? 'Черновой пакет готов'
              : 'Финальный пакет готов'
          }
          value={`Версия плана ${release.plan_version}`}
        />
        <p className="m-0 text-xs text-neutral-600">
          Прогноз в пакете: {horizonLabel(release.scene_horizon).toLowerCase()}
        </p>
      </section>
      {release.plan_version !== plan.version && (
        <InlineMessage tone="warning">
          Этот пакет содержит версию {release.plan_version}. Текущий план —
          версия {plan.version}. Для актуальных файлов соберите новый пакет.
        </InlineMessage>
      )}
      {geometryVersion !== undefined &&
        release.geometry_version !== geometryVersion && (
          <InlineMessage tone="warning">
            После создания пакета изменились участки или исходная геометрия. Для
            актуальных файлов соберите новый пакет.
          </InlineMessage>
        )}
      <ReleaseDownloads
        artifacts={release.artifacts ?? []}
        onDownload={onDownload}
      />
      {Boolean(release.warnings?.length) && (
        <Disclosure
          variant="plain"
          title={`Ограничения пакета (${release.warnings?.length})`}
        >
          <ul className="m-0 grid gap-2 pl-4">
            {release.warnings?.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Disclosure>
      )}
      <Button variant="secondary" disabled={loading} onClick={onOpenForm}>
        {hasDraft ? 'Продолжить черновик' : 'Собрать новый пакет'}
      </Button>
    </>
  );
};
