/** Исходные данные: сводка назначений и отдельное участие границ в подготовке */
import type { Layer, LayerMapping, LayerRecognition } from '@green/api-client';
import {
  Disclosure,
  IconButton,
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
} from '@green/ui';
import { Info } from 'lucide-react';
import { completedRecognitionLayerIds } from '../model/recognitionProgress';
import {
  isBoundaryTopologyIntent,
  requiresLayerRoleReview,
  toLayerMapping,
} from '../model/layerKinds';

interface Props {
  layers: Layer[];
  mappings: Record<string, LayerMapping>;
  recognition?: LayerRecognition;
  preview?: boolean;
}

/** Сводка считает физические назначения отдельно от намерения замыкания */
export function LayerMappingSummary({
  layers,
  mappings,
  recognition,
  preview,
}: Props) {
  const checkpoint = completedRecognitionLayerIds(layers, recognition);
  const recognitionPreview = preview || recognition?.status === 'running';
  const countedLayers =
    recognitionPreview && checkpoint
      ? layers.filter((layer) => checkpoint.has(layer.id))
      : layers;
  const pending = countedLayers.filter((layer) =>
    requiresLayerRoleReview(mappings[layer.id] ?? toLayerMapping(layer)),
  );
  const topology = countedLayers.filter((layer) =>
    isBoundaryTopologyIntent(mappings[layer.id] ?? toLayerMapping(layer)),
  );
  const reasons = pending.reduce(
    (counts, layer) => {
      const proposal = recognition?.proposals.find(
        (item) => item.layer_id === layer.id,
      );
      const category = recognition?.categories.find(
        (item) => item.category === proposal?.category,
      );
      if (category?.kind === 'site_border') counts.boundary += 1;
      else if (category && category.kind === null) counts.descriptive += 1;
      else counts.uncertain += 1;
      return counts;
    },
    { boundary: 0, descriptive: 0, uncertain: 0 },
  );
  return (
    <div className="grid gap-3 text-sm leading-5">
      <dl className="m-0 grid grid-cols-[1fr_auto] gap-x-4 gap-y-2">
        <dt className="text-neutral-500">
          {recognitionPreview ? 'Определено' : 'Принято'}
        </dt>
        <dd className="m-0 tabular-nums">
          {countedLayers.length - pending.length - topology.length} из{' '}
          {layers.length}
        </dd>
        {topology.length > 0 && (
          <>
            <dt className="text-neutral-500">Для замыкания</dt>
            <dd className="m-0 tabular-nums">{topology.length}</dd>
          </>
        )}
        <dt className="text-neutral-500">Проверить</dt>
        <dd className="m-0 tabular-nums">{pending.length}</dd>
      </dl>
      {pending.length > 0 && (
        <Disclosure variant="plain" title="Причины проверки">
          <dl className="m-0 grid grid-cols-[1fr_auto] gap-x-4 gap-y-2 text-neutral-600">
            {reasons.boundary > 0 && (
              <>
                <dt>Границы</dt>
                <dd className="m-0 tabular-nums">{reasons.boundary}</dd>
              </>
            )}
            {reasons.descriptive > 0 && (
              <>
                <dt>Без расчётной роли</dt>
                <dd className="m-0 tabular-nums">{reasons.descriptive}</dd>
              </>
            )}
            {reasons.uncertain > 0 && (
              <>
                <dt>Другие</dt>
                <dd className="m-0 tabular-nums">{reasons.uncertain}</dd>
              </>
            )}
          </dl>
        </Disclosure>
      )}
    </div>
  );
}

/** Самостоятельный список слоёв открывает ту же сводку назначений */
export function LayerMappingInformation(props: Props) {
  return (
    <Popover>
      <PopoverTrigger
        render={
          <IconButton
            icon={Info}
            label="О распознавании слоёв"
            variant="ghost"
            controlSize="default"
          />
        }
      />
      <PopoverPopup align="end" size="compact" aria-label="Распознавание слоёв">
        <PopoverTitle className="m-0 mb-3 text-sm font-medium">
          Распознавание слоёв
        </PopoverTitle>
        <LayerMappingSummary {...props} />
      </PopoverPopup>
    </Popover>
  );
}
