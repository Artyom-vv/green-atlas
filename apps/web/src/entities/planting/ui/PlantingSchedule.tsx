import { useMemo, type FC } from 'react';
import type { PlanObject } from '@green/api-client';
import {
  IconButton,
  ScrollTable,
  ScrollTableRow,
  ScrollTableCell,
  ScrollTableColumnHeader,
} from '@green/ui';
import { Crosshair } from 'lucide-react';
import { ResultPanel } from '@/shared/ui/results/ResultPanel';
import { plantingSchedule } from '../model/schedule';
import { RESULTS_CONTENT_INSET } from '@/shared/ui/results/resultsLayout';

export interface PlantingScheduleProps {
  objects: PlanObject[];
  speciesNames: Map<string, string>;
  onSelect: (ids: string[]) => void;
  embedded?: boolean;
}
export const PlantingSchedule: FC<PlantingScheduleProps> = ({
  objects,
  speciesNames,
  onSelect,
  embedded,
}) => {
  const rows = useMemo(
    () => plantingSchedule(objects, speciesNames),
    [objects, speciesNames],
  );
  return (
    <ResultPanel
      embedded={embedded}
      title="Посадочная ведомость"
      label="Посадочная ведомость"
      count={`Всего посадок: ${objects.length}`}
      body={
        rows.length ? (
          <ScrollTable
            aria-label="Посадочная ведомость"
            columns="minmax(0, 1fr) 7rem 5rem 3.5rem"
            minWidth="28rem"
            containerClassName="flex-1"
            header={
              <>
                <ScrollTableColumnHeader
                  className={`flex min-h-10 items-center ${RESULTS_CONTENT_INSET} py-1`}
                >
                  Вид
                </ScrollTableColumnHeader>
                <ScrollTableColumnHeader className="flex min-h-10 items-center py-1">
                  Тип
                </ScrollTableColumnHeader>
                <ScrollTableColumnHeader className="flex min-h-10 items-center justify-end py-1">
                  Посадок
                </ScrollTableColumnHeader>
                <ScrollTableColumnHeader className="min-h-10 py-1">
                  <span className="sr-only">На карте</span>
                </ScrollTableColumnHeader>
              </>
            }
          >
            {rows.map((row) => (
              <ScrollTableRow key={row.key}>
                <ScrollTableCell className={`${RESULTS_CONTENT_INSET} py-1`}>
                  {row.name}
                </ScrollTableCell>
                <ScrollTableCell className="py-1">{row.kind}</ScrollTableCell>
                <ScrollTableCell className="py-1 text-right font-mono tabular-nums">
                  {row.ids.length}
                </ScrollTableCell>
                <ScrollTableCell className="px-3 py-1">
                  <IconButton
                    variant="ghost"
                    controlSize="compact"
                    icon={Crosshair}
                    label={`Показать на карте: ${row.name}, ${row.kind.toLowerCase()}, посадок: ${row.ids.length}`}
                    onClick={() => onSelect(row.ids)}
                  />
                </ScrollTableCell>
              </ScrollTableRow>
            ))}
          </ScrollTable>
        ) : (
          <p
            role="status"
            className={`${RESULTS_CONTENT_INSET} py-4 text-xs text-neutral-600`}
          >
            Посадок пока нет
          </p>
        )
      }
    />
  );
};
