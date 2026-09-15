import type { FC } from 'react';
import { Disclosure, InlineMessage } from '@green/ui';
import { LayerMappingTable } from '@/entities/source-data/ui/LayerMappingTable';
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
}) => (
  <>
    {' '}
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
      <InlineMessage tone="error" title="Нужен рабочий фрагмент">
        Часть объектов не попала на карту. Исключите эти слои из ограничений или
        загрузите меньший фрагмент DXF.
      </InlineMessage>
    )}
    {!reviewOnly && !hasPlanningBoundary && (
      <InlineMessage tone="info" title="Границу можно задать на карте">
        В DXF нет замкнутой границы участка. После подготовки карты обведите
        рабочую область вручную; ограничения от подтверждённых слоёв всё равно
        останутся видны.
      </InlineMessage>
    )}
    <LayerMappingTable
      readOnly={reviewOnly || preparationBlocked}
      layers={layers}
      mappings={mappings}
      onChange={setMappings}
    />
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
