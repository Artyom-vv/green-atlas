import type { FC } from 'react';
import type { PlanObject, ValidationIssue } from '@green/api-client';
import { Crosshair, Lock } from 'lucide-react';
import { IconButton, InlineMessage } from '@green/ui';
import type { GrowthHorizon } from '@/entities/planting-forecast';
import { EditorPanel } from '@/shared/ui/inspector';
import { EditorGrowth } from '@/entities/planting-forecast/ui/PlantingGrowth';
import { selectionProblems } from '../model/selectionProblems';
import { SelectionActions } from './SelectionActions';
import { SelectionIssues } from './SelectionIssues';

export interface GroupInspectorProps {
  mapMode?: '2d' | '3d';
  objects: PlanObject[];
  issues?: ValidationIssue[];
  disabled?: boolean;
  growthHorizon?: GrowthHorizon;
  onGrowthHorizon: (value: GrowthHorizon) => void;
  onSpecies: () => void;
  onCopy: () => void;
  onMove?: () => void;
  onFit?: () => void;
  onLock: (locked: boolean) => void;
  onDelete: () => void;
}
export const GroupInspector: FC<GroupInspectorProps> = ({
  objects,
  issues,
  disabled,
  growthHorizon,
  onGrowthHorizon,
  onSpecies,
  onCopy,
  onMove,
  onFit,
  onLock,
  onDelete,
  mapMode = '2d',
}) => {
  const locked = objects.filter((object) => object.locked).length;
  const trees = objects.filter((object) => object.kind === 'tree').length;
  const assigned = objects.filter(
    (object) => object.species_revision_id,
  ).length;
  return (
    <EditorPanel
      title={`Выбрано ${objects.length}`}
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
      <section className="grid gap-3" aria-label="Состав выделения">
        <div className="flex flex-wrap gap-2 text-xs">
          {trees > 0 && <span>Деревья {trees}</span>}
          {!!(objects.length - trees) && (
            <span>Кустарники {objects.length - trees}</span>
          )}
        </div>
        <dl className="m-0 grid grid-cols-2 gap-3 text-xs [&_dd]:m-0 [&_dd]:mt-1 [&_dd]:font-medium [&_dt]:text-neutral-600">
          <div>
            <dt>Виды назначены</dt>
            <dd>
              {assigned} из {objects.length}
            </dd>
          </div>
          <div>
            <dt>Закреплены</dt>
            <dd>{locked}</dd>
          </div>
        </dl>
      </section>
      {locked > 0 && (
        <InlineMessage tone="info">
          <span className="flex gap-2">
            <Lock className="shrink-0" size={14} aria-hidden="true" />
            Для изменения посадок снимите закрепление.
          </span>
        </InlineMessage>
      )}
      <SelectionActions
        label="Действия с выделением"
        speciesLabel="Назначить виды"
        disabled={disabled}
        editDisabled={locked > 0}
        mapMode={mapMode}
        onSpecies={onSpecies}
        onMove={onMove}
        onCopy={onCopy}
        onDelete={onDelete}
        onLock={() => onLock(!locked)}
        locked={locked > 0}
        lockLabel={
          locked
            ? locked < objects.length
              ? `Открепить ${locked}`
              : 'Открепить'
            : 'Закрепить'
        }
      />
      <SelectionIssues problems={selectionProblems(objects, issues)} />
      <EditorGrowth
        objects={objects}
        value={growthHorizon}
        onChange={onGrowthHorizon}
        showControl={mapMode === '2d'}
      />
    </EditorPanel>
  );
};
