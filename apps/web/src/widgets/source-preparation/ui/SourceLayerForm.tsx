import type { FC } from 'react';
import { Disclosure, InlineMessage } from '@green/ui';
import { LayerMappingTable } from '@/entities/source-data/ui/LayerMappingTable';
import { BoundaryCandidatePicker } from '@/entities/source-data/ui/BoundaryCandidatePicker';
import { DataPassportPanel } from '@/entities/source-data/ui/DataPassportPanel';
import { ProjectConflictNotice } from '@/entities/project/ui/ProjectConflictNotice';
import { isProjectConflict } from '@/entities/project/model/projectConflict';
import type { SourceLayerFormProps } from './SourceLayerForm.props';
export const SourceLayerForm: FC<SourceLayerFormProps> = ({
  sourceWarnings,
  incompleteConstraintLayers,
  hasPlanningBoundary,
  reviewOnly,
  preparationBlocked,
  layers,
  mappings,
  setMappings,
  dataPassportQuery,
  mutationError,
  reloadAfterConflict,
  projectQuery,
}) => {
  const hasBoundaryCandidates = layers.some(
    (layer) => layer.boundary_candidate?.status === 'usable',
  );
  return (
    <>
      {!reviewOnly && (
        <BoundaryCandidatePicker
          readOnly={preparationBlocked}
          layers={layers}
          mappings={mappings}
          onChange={setMappings}
        />
      )}
      {!!sourceWarnings.length && (
        <Disclosure
          variant="plain"
          title={`Замечания к файлу (${sourceWarnings.length})`}
        >
          <ul className="m-0 list-disc space-y-1 pl-5 text-xs">
            {sourceWarnings.map((warning) => (
              <li className="wrap-anywhere" key={warning}>
                {warning}
              </li>
            ))}
          </ul>
        </Disclosure>
      )}
      {!reviewOnly && !!incompleteConstraintLayers.length && (
        <InlineMessage
          tone="warning"
          title="Нужно уточнить геометрию отдельных слоёв"
        >
          Исходные объекты сохранены в DXF, но часть не представлена расчётными
          контурами. Можно открыть редактор без расчёта и продолжить работу.
          Назначение слоёв и ограничения нужно проверить до автоматической
          расстановки.
        </InlineMessage>
      )}
      {!reviewOnly && !hasPlanningBoundary && !hasBoundaryCandidates && (
        <InlineMessage tone="info" title="Границу можно задать на карте">
          В DXF нет замкнутой границы участка. После подготовки карты обведите
          рабочую область вручную; ограничения от подтверждённых слоёв всё равно
          останутся видны.
        </InlineMessage>
      )}
      {hasBoundaryCandidates && !reviewOnly ? (
        <Disclosure variant="plain" title={`Другие слои (${layers.length})`}>
          <LayerMappingTable
            readOnly={preparationBlocked}
            layers={layers}
            mappings={mappings}
            onChange={setMappings}
          />
        </Disclosure>
      ) : (
        <LayerMappingTable
          readOnly={reviewOnly || preparationBlocked}
          layers={layers}
          mappings={mappings}
          onChange={setMappings}
        />
      )}
      {dataPassportQuery.data && (
        <Disclosure variant="plain" title="Полнота исходных данных">
          <DataPassportPanel passport={dataPassportQuery.data} header={null} />
        </Disclosure>
      )}
      {!!mutationError &&
        (isProjectConflict(mutationError) ? (
          <ProjectConflictNotice
            error={mutationError}
            onReload={() => void reloadAfterConflict()}
            reloading={projectQuery.isFetching}
          />
        ) : (
          <InlineMessage tone="error">
            Не удалось подготовить карту. Проверьте слои и повторите попытку.
          </InlineMessage>
        ))}
    </>
  );
};
