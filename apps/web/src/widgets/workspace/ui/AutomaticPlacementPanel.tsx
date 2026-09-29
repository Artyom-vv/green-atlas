import { useState, type FC } from 'react';
import { Button, Field, FormActions, InlineMessage, NumberInput, Select } from '@green/ui';
import { PlacementToolSurface } from '@/shared/ui/TaskSurface';
import { errorMessage } from '@/shared/errors/errorMessage';
import type { WorkspaceRecommendationToolProps } from './WorkspaceRecommendationTool.props';

type Props = Pick<WorkspaceRecommendationToolProps,
  'applyChanges' | 'automaticPreview' | 'previewAutomatic' | 'project' |
  'selectedPatternZoneIds' | 'speciesNames'> & { onDetailed: () => void };

export const AutomaticPlacementPanel: FC<Props> = ({
  applyChanges, automaticPreview, previewAutomatic, project,
  selectedPatternZoneIds, speciesNames, onDetailed,
}) => {
  const [chosenZone, setChosenZone] = useState('');
  const [near, setNear] = useState<'building' | 'road' | 'area'>('area');
  const [kind, setKind] = useState<'auto' | 'tree' | 'shrub' | 'mixed'>('auto');
  const [count, setCount] = useState(8);
  const zones = (project.planting_zones ?? []).filter((zone) => zone.id);
  const zoneId = zones.find((zone) => zone.id === chosenZone)?.id ??
    zones.find((zone) => selectedPatternZoneIds.includes(zone.id!))?.id ??
    zones[0]?.id;
  const found = automaticPreview?.found ?? 0;
  const names = automaticPreview?.species_revision_ids
    .map((id) => speciesNames.get(id) ?? id).join(', ');
  const submit = () => {
    if (!zoneId || !project.plan) return;
    previewAutomatic.mutate({
      base_plan_version: project.plan.version,
      zone_id: zoneId,
      near,
      plant_kind: kind,
      target_count: count,
    });
  };
  if (automaticPreview) {
    return <PlacementToolSurface title="Предложение готово" showHeader={false} footer={
      <FormActions layout="equal" minItemWidth="10rem">
        <Button variant="secondary" disabled={applyChanges.isPending}
          onClick={() => previewAutomatic.reset()}>Изменить</Button>
        <Button variant="primary" loading={applyChanges.isPending}
          disabled={!automaticPreview.change_set?.can_apply || !found}
          onClick={() => {
            if (automaticPreview.change_set?.can_apply)
              applyChanges.mutate(automaticPreview.change_set);
          }}>Добавить {found}</Button>
      </FormActions>
    }>
      <h3 className="m-0 text-base font-semibold">Найдено {found} из {automaticPreview.requested}</h3>
      {names && <p className="m-0 text-sm text-neutral-700">{names}</p>}
      {automaticPreview.shortfall > 0 && <InlineMessage tone="warning">
        Для остальных мест не найдено подходящих позиций. Можно изменить участок или условия.
      </InlineMessage>}
      <p className="m-0 text-xs leading-5 text-neutral-600">{automaticPreview.selection_basis}</p>
      {Boolean(applyChanges.error) && <InlineMessage tone="error">{errorMessage(applyChanges.error)}</InlineMessage>}
    </PlacementToolSurface>;
  }
  return <PlacementToolSurface title="Автоматическая посадка" showHeader={false} footer={
    <FormActions layout="equal" minItemWidth="10rem">
      <Button variant="secondary" disabled={previewAutomatic.isPending} onClick={onDetailed}>
        Подбор по цели
      </Button>
      <Button variant="primary" loading={previewAutomatic.isPending}
        disabled={!zoneId || !project.plan || count < 1 || count > 100}
        onClick={submit}>Найти места</Button>
    </FormActions>
  }>
    <p className="m-0 text-sm text-neutral-600">Укажите участок и количество. Приложение подберёт тип и покажет результат до сохранения.</p>
    <Field label="Участок">
      <Select aria-label="Участок для автопосадки" value={zoneId ?? ''}
        onChange={(event) => setChosenZone(event.target.value)}>
        {zones.length === 0 && <option value="">Сначала создайте участок</option>}
        {zones.map((zone) => <option key={zone.id} value={zone.id}>{zone.label}</option>)}
      </Select>
    </Field>
    <Field label="Рядом с">
      <Select aria-label="Ориентир для автопосадки" value={near}
        onChange={(event) => setNear(event.target.value as typeof near)}>
        <option value="area">По всему участку</option>
        <option value="building">Зданиями</option>
        <option value="road">Дорогой</option>
      </Select>
    </Field>
    <Field label="Тип растений">
      <Select aria-label="Тип растений для автопосадки" value={kind}
        onChange={(event) => setKind(event.target.value as typeof kind)}>
        <option value="auto">Подобрать автоматически</option>
        <option value="tree">Деревья</option>
        <option value="shrub">Кустарники</option>
        <option value="mixed">Деревья и кустарники</option>
      </Select>
    </Field>
    <Field label="Сколько посадок">
      <NumberInput aria-label="Количество автопосадок" min={1} max={100}
        value={count} onValueChange={(value) => setCount(value ?? 0)} />
    </Field>
    {Boolean(previewAutomatic.error) && <InlineMessage tone="error">
      {errorMessage(previewAutomatic.error)}
    </InlineMessage>}
  </PlacementToolSurface>;
};
