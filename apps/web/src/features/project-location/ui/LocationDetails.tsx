import type { LocationAlignment, LocationDraft } from '@green/api-client';
import { Button, Popover, PopoverPopup, PopoverTrigger } from '@green/ui';
import { Info } from 'lucide-react';
import { locationMetric } from '../model/locationPresentation';

export function LocationDetails({
  draft,
  alignment,
  disabled,
}: {
  draft: LocationDraft;
  alignment?: LocationAlignment;
  disabled?: boolean;
}) {
  const overlap = alignment?.provenance?.overlap_check as
    { matched_building_count?: number } | undefined;
  const preview = alignment?.preview_buildings as
    { features: unknown[]; omitted_outside_extent_count?: number } | undefined;
  const cad = draft.provenance?.cad as
    | {
        feature_count?: number;
        omitted_review_features?: number;
        semantic_status?: string;
      }
    | undefined;
  const saved = alignment?.method === 'saved_frame';
  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button
            variant="ghost"
            disabled={disabled}
            className="text-neutral-600 hover:text-neutral-800"
            startIcon={<Info />}
          />
        }
      >
        Показать сведения
      </PopoverTrigger>
      <PopoverPopup
        align="start"
        side="top"
        className="w-96 max-w-(--available-width)"
      >
        <div className="mb-3 font-medium">Основание совмещения</div>
        <dl className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-3 text-xs">
          <dt className="text-neutral-600">
            {saved ? 'Совпадения контуров' : 'Опорные пары'}
          </dt>
          <dd className="m-0">
            {saved
              ? (overlap?.matched_building_count ?? 0)
              : (alignment?.inlier_count ?? 0)}
          </dd>
          <dt className="text-neutral-600">Здания в наложении</dt>
          <dd className="m-0">{preview?.features.length ?? 0}</dd>
          <dt className="text-neutral-600">Метод</dt>
          <dd className="m-0">
            {saved
              ? 'Сохранённая привязка'
              : alignment?.method === 'manual'
                ? 'Опоры и контроль'
                : 'Адресные соответствия'}
          </dd>
          <dt className="text-neutral-600">
            {saved ? 'Прежняя средняя ошибка' : 'Средняя ошибка'}
          </dt>
          <dd className="m-0">{locationMetric(alignment?.rmse_m)}</dd>
          <dt className="text-neutral-600">
            {saved ? 'Прежняя максимальная' : 'Максимальная ошибка'}
          </dt>
          <dd className="m-0">{locationMetric(alignment?.max_residual_m)}</dd>
          <dt className="text-neutral-600">Независимая проверка</dt>
          <dd className="m-0">{locationMetric(alignment?.holdout_rmse_m)}</dd>
        </dl>
        <dl className="mt-4 grid grid-cols-[1fr_auto] gap-x-6 gap-y-3 border-t border-neutral-200 pt-3 text-xs">
          <dt className="text-neutral-600">Вне привязки</dt>
          <dd className="m-0">
            {preview?.omitted_outside_extent_count ?? 0} зданий
          </dd>
          <dt className="text-neutral-600">Обзор CAD</dt>
          <dd className="m-0">
            {cad?.feature_count ?? draft.cad_geometry.features.length} /{' '}
            {(cad?.feature_count ?? draft.cad_geometry.features.length) +
              (cad?.omitted_review_features ?? 0)}
          </dd>
          {cad?.semantic_status && cad.semantic_status !== 'loaded' && (
            <>
              <dt className="text-neutral-600">Адресная семантика</dt>
              <dd className="m-0">
                {cad.semantic_status === 'partial' ? 'Частичная' : 'Нет данных'}
              </dd>
            </>
          )}
        </dl>
      </PopoverPopup>
    </Popover>
  );
}
