import type {
  AssortmentEntry,
  AssortmentInventory,
  SpeciesRevision,
} from '@green/api-client';
import { ExternalLink, Ruler, Trees } from 'lucide-react';
import { cx } from '@green/ui';
import { crownLabels, territoryLabels } from '../model/assortmentLabels';
import { speciesPhotos } from '../model/speciesPhotos';
import { SpeciesPhoto } from './SpeciesPhoto';

export function SpeciesInspector({
  species,
  entry,
  inventory,
  reason,
  showHeading = true,
  reasonTone = 'neutral',
}: {
  species?: SpeciesRevision;
  entry?: AssortmentEntry;
  inventory?: AssortmentInventory;
  reason?: string;
  showHeading?: boolean;
  reasonTone?: 'neutral' | 'warning';
}) {
  if (!species && !entry)
    return (
      <p className="p-4 text-sm text-neutral-600">
        Выберите растение в списке.
      </p>
    );
  return (
    <div className="grid min-w-0 gap-5 p-4">
      {showHeading && (
        <div className="grid gap-1">
          <span className="text-xs text-neutral-600">
            {(species?.kind ?? entry?.kind) === 'tree'
              ? 'Дерево'
              : entry?.kind === 'vine'
                ? 'Лиана'
                : 'Кустарник'}
          </span>
          <h3 className="m-0 text-lg font-semibold">
            {species?.common_name ?? entry?.name}
          </h3>
          {species && (
            <p className="m-0 text-xs text-neutral-600 italic">
              {species.scientific_name}
            </p>
          )}
        </div>
      )}
      {reason && (
        <p
          className={cx(
            'm-0 border-l-2 pl-3 text-xs leading-5',
            reasonTone === 'warning'
              ? 'border-amber-500'
              : 'border-neutral-300',
          )}
        >
          {reason}
        </p>
      )}
      {inventory && species && !entry && (
        <p className="m-0 border-l-2 border-amber-500 pl-3 text-xs leading-5">
          Соответствие этого вида московскому ассортименту ещё не подтверждено.
        </p>
      )}
      {species ? (
        <>
          {speciesPhotos[species.species_id] && (
            <SpeciesPhoto key={species.id} species={species} credits />
          )}
          <dl className="m-0 grid grid-cols-2 gap-4 border-y border-neutral-200 py-4">
            <div>
              <dt className="flex items-center gap-1 text-xs text-neutral-600">
                <Ruler size={14} /> Высота
              </dt>
              <dd className="m-0 mt-1 text-base tabular-nums">
                {species.mature_height_min_m.toLocaleString('ru', {
                  maximumFractionDigits: 1,
                })}
                –
                {species.mature_height_max_m.toLocaleString('ru', {
                  maximumFractionDigits: 1,
                })}{' '}
                м
              </dd>
            </div>
            <div>
              <dt className="flex items-center gap-1 text-xs text-neutral-600">
                <Trees size={14} /> Диаметр кроны
              </dt>
              <dd className="m-0 mt-1 text-base tabular-nums">
                {species.mature_crown_diameter_min_m.toLocaleString('ru', {
                  maximumFractionDigits: 1,
                })}
                –
                {species.mature_crown_diameter_max_m.toLocaleString('ru', {
                  maximumFractionDigits: 1,
                })}{' '}
                м
              </dd>
            </div>
            <div>
              <dt className="text-xs text-neutral-600">Форма кроны</dt>
              <dd className="m-0 mt-1 text-sm">
                {crownLabels[species.crown_shape]}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-neutral-600">Рост</dt>
              <dd className="m-0 mt-1 text-sm">
                {
                  { slow: 'Медленный', moderate: 'Умеренный', fast: 'Быстрый' }[
                    species.growth_rate
                  ]
                }
              </dd>
            </div>
          </dl>
          <p className="m-0 text-xs leading-5 text-neutral-600">
            Размеры взрослого растения. На карте отдельно показаны посадочное
            место и прогноз кроны.
          </p>
        </>
      ) : (
        <p className="m-0 text-sm leading-6 text-neutral-600">
          Вид есть в официальном ассортименте. Для посадки в сервисе пока не
          подтверждены расчётные характеристики.
        </p>
      )}
      {entry && inventory && (
        <section className="grid gap-2" aria-label="Рекомендации по территории">
          <h4 className="m-0 text-sm font-semibold">Где рекомендуется</h4>
          <dl className="m-0 grid gap-2">
            {inventory.categories.map((category, index) => (
              <div
                key={category}
                className="flex justify-between gap-3 text-xs"
              >
                <dt>
                  {territoryLabels[category as keyof typeof territoryLabels] ??
                    category}
                </dt>
                <dd className="m-0 shrink-0 text-neutral-600">
                  {entry.cells[index] === '+'
                    ? 'Да'
                    : entry.cells[index] === '-'
                      ? 'Нет'
                      : 'Нет данных'}
                </dd>
              </div>
            ))}
          </dl>
          {!entry.matrix_reviewed && (
            <p className="m-0 text-xs leading-5 text-neutral-600">
              Значения перенесены из таблицы. Эта строка ещё не прошла сверку
              для расчёта.
            </p>
          )}
          {entry.notes?.map((note) => (
            <p className="m-0 text-xs leading-5" key={note}>
              {note}
            </p>
          ))}
          {!entry.conditions_reviewed && (
            <p className="m-0 text-xs text-neutral-600">
              Примечания этой строки ещё не сверены.
            </p>
          )}
          <a
            className="flex items-center gap-1 text-xs text-blue-700 underline"
            href={`${inventory.source_url}#page=${entry.page}`}
            target="_blank"
            rel="noreferrer"
          >
            Ассортимент Москвы, стр. {entry.page}, строка {entry.row}
            <ExternalLink size={12} />
          </a>
        </section>
      )}
      {species && (
        <details className="text-xs leading-5">
          <summary className="cursor-pointer text-neutral-600">
            Источники и точность данных
          </summary>
          <p>{species.evidence_note}</p>
          {species.source_urls.map((url, i) => (
            <a
              key={url}
              href={url}
              target="_blank"
              rel="noreferrer"
              className="mr-3 text-blue-700 underline"
            >
              Источник {i + 1}
            </a>
          ))}
        </details>
      )}
    </div>
  );
}
