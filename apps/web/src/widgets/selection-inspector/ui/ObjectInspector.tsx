import type { FC } from 'react';
import type { PlanObject, ValidationIssue } from '@green/api-client';
import { Crosshair, Lock } from 'lucide-react';
import { IconButton, InlineMessage } from '@green/ui';
import type { GrowthHorizon } from '@/entities/planting-forecast';
import { EditorPanel } from '@/shared/ui/inspector';
import { EditorGrowth } from '@/entities/planting-forecast/ui/PlantingGrowth';
import { SelectionActions } from './SelectionActions';
import { selectionProblems } from '../model/selectionProblems';
import { SelectionIssues } from './SelectionIssues';

export interface ObjectInspectorProps {
  object?: PlanObject;
  issues?: ValidationIssue[];
  speciesName?: string;
  growthHorizon?: GrowthHorizon;
  onGrowthHorizon: (value: GrowthHorizon) => void;
  onSpecies: () => void;
  onDelete: () => void;
  onFit?: () => void;
  onMove?: () => void;
  onLock?: (locked: boolean) => void;
  editable?: boolean;
  mapMode?: '2d' | '3d';
}
export const ObjectInspector: FC<ObjectInspectorProps> = ({
  object,
  issues,
  speciesName,
  growthHorizon,
  onGrowthHorizon,
  onSpecies,
  onDelete,
  onFit,
  onMove,
  onLock,
  editable = true,
  mapMode = '2d',
}) => {
  if (!object)
    return (
      <EditorPanel title="Выделение">
        <p className="m-0 text-xs text-neutral-600">Ничего не выбрано</p>
      </EditorPanel>
    );
  const status =
    object.status === 'error'
      ? 'Есть нарушение'
      : object.status === 'warning'
        ? 'Есть замечания'
        : 'Размещение допустимо';
  const kind = object.kind === 'tree' ? 'Дерево' : 'Кустарник';
  return (
    <EditorPanel
      title="Выбрано 1"
      headerActions={
        onFit ? (
          <IconButton
            variant="ghost"
            icon={Crosshair}
            label="К выделению"
            onClick={onFit}
          />
        ) : undefined
      }
    >
      <section className="grid gap-3" aria-label="Выбранная посадка">
        <div className="grid gap-1">
          <h3 className="m-0 text-sm font-semibold">{speciesName ?? kind}</h3>
          <p className="m-0 text-xs text-neutral-600">
            {speciesName ? kind : 'Вид не назначен'}
          </p>
        </div>
        <dl className="m-0 grid gap-2 text-xs [&_dd]:m-0 [&_dt]:text-neutral-600 [&>div]:flex [&>div]:flex-wrap [&>div]:justify-between [&>div]:gap-2">
          <div>
            <dt>Проверка</dt>
            <dd
              data-severity={object.status}
              className="data-[severity=error]:text-error data-[severity=warning]:text-warning-strong"
            >
              {status}
            </dd>
          </div>
          <div>
            <dt>Закреплено</dt>
            <dd>{object.locked ? 'Да' : 'Нет'}</dd>
          </div>
        </dl>
      </section>
      {object.locked && (
        <InlineMessage tone="info">
          <span className="flex gap-2">
            <Lock className="shrink-0" size={14} aria-hidden="true" />
            Посадка закреплена. Для изменения снимите закрепление.
          </span>
        </InlineMessage>
      )}
      {(editable || onLock) && (
        <SelectionActions
          label="Действия с посадкой"
          speciesLabel={speciesName ? 'Изменить вид' : 'Назначить вид'}
          mapMode={mapMode}
          onSpecies={editable ? onSpecies : undefined}
          onMove={editable ? onMove : undefined}
          onDelete={editable ? onDelete : undefined}
          locked={object.locked}
          onLock={onLock ? () => onLock(!object.locked) : undefined}
        />
      )}
      <SelectionIssues problems={selectionProblems([object], issues)} />
      <EditorGrowth
        objects={[object]}
        value={growthHorizon}
        onChange={onGrowthHorizon}
        showControl={mapMode === '2d'}
      />
    </EditorPanel>
  );
};
