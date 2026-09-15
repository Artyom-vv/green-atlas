import { useMemo, type FC } from 'react';
import type { PlanObject } from '@green/api-client';
import {
  Button,
  ScrollTable,
  ScrollTableRow,
  ScrollTableCell,
  ScrollTableColumnHeader,
} from '@green/ui';
import { Crosshair } from 'lucide-react';
import { ResultPanel } from '@/shared/ui/results/ResultPanel';
import { plantingSchedule } from '../model/schedule';

export interface PlantingScheduleProps {
  objects: PlanObject[];
  speciesNames: Map<string, string>;
  onSelect: (ids: string[]) => void;
}
export const PlantingSchedule: FC<PlantingScheduleProps> = ({
  objects,
  speciesNames,
  onSelect,
}) => {
  const rows = useMemo(
    () => plantingSchedule(objects, speciesNames),
    [objects, speciesNames],
  );
  return (
    <ResultPanel
      title="Посадочная ведомость"
      label="Посадочная ведомость"
      count={`Всего посадок: ${objects.length}`}
      body={
        <ScrollTable
          aria-label="Посадочная ведомость"
          columns="minmax(0, 1fr) 7rem 5rem 8rem"
          minWidth="32rem"
          containerClassName="flex-1"
          header={
            <>
              {['Вид', 'Тип', 'Посадок'].map((label) => (
                <ScrollTableColumnHeader key={label}>
                  {label}
                </ScrollTableColumnHeader>
              ))}
              <ScrollTableColumnHeader>
                <span className="sr-only">Действие</span>
              </ScrollTableColumnHeader>
            </>
          }
        >
          {rows.map((row) => (
            <ScrollTableRow key={row.key}>
              <ScrollTableCell>{row.name}</ScrollTableCell>
              <ScrollTableCell>{row.kind}</ScrollTableCell>
              <ScrollTableCell className="tabular-nums">
                {row.ids.length}
              </ScrollTableCell>
              <ScrollTableCell className="text-right">
                <Button
                  variant="ghost"
                  startIcon={<Crosshair />}
                  aria-label={`Показать на карте: ${row.name}, ${row.kind.toLowerCase()}, посадок: ${row.ids.length}`}
                  onClick={() => onSelect(row.ids)}
                >
                  На карте
                </Button>
              </ScrollTableCell>
            </ScrollTableRow>
          ))}
        </ScrollTable>
      }
    />
  );
};
