import type { Meta, StoryObj } from '@storybook/react';
import { useState, type FC } from 'react';
import { Button, ControlProvider, FormActions } from '../index';
import { ScrollTable } from './ScrollTable';
import {
  ScrollTableCell,
  ScrollTableColumnHeader,
  ScrollTableRow,
} from './ScrollTableParts';

const rowCounts = [100, 3, 0] as const;
const labels = [
  'Липа мелколистная — посадки вдоль длинной пешеходной аллеи',
  'Клён остролистный',
  'Дёрен белый — группа у северного входа',
] as const;

interface ScrollTableExampleProps {
  initialCount: number;
  width: number;
}

const ScrollTableExample: FC<ScrollTableExampleProps> = ({
  initialCount,
  width,
}) => {
  const [count, setCount] = useState(initialCount);
  const [selected, setSelected] = useState<number>();
  return (
    <ControlProvider size="compact">
      <section className="grid max-w-full gap-3" style={{ width }}>
        <FormActions className="justify-start" aria-label="Количество строк">
          {rowCounts.map((value) => (
            <Button
              key={value}
              variant={count === value ? 'primary' : 'secondary'}
              aria-pressed={count === value}
              onClick={() => setCount(value)}
            >
              {value} строк
            </Button>
          ))}
        </FormActions>
        <ScrollTable
          aria-label="Посадки"
          columns="minmax(200px, 1fr) 160px 112px"
          minWidth={540}
          containerClassName="h-80"
          header={
            <>
              <ScrollTableColumnHeader>Название</ScrollTableColumnHeader>
              <ScrollTableColumnHeader>Участок</ScrollTableColumnHeader>
              <ScrollTableColumnHeader>Действие</ScrollTableColumnHeader>
            </>
          }
        >
          {Array.from({ length: count }, (_, index) => (
            <ScrollTableRow key={index} data-selected={selected === index}>
              <ScrollTableCell>
                {index + 1}. {labels[index % labels.length]}
              </ScrollTableCell>
              <ScrollTableCell>
                Северный участок у существующей застройки
              </ScrollTableCell>
              <ScrollTableCell>
                <Button
                  variant="ghost"
                  aria-label={`Показать посадку ${index + 1}`}
                  onClick={() => setSelected(index)}
                >
                  Показать
                </Button>
              </ScrollTableCell>
            </ScrollTableRow>
          ))}
          {count === 0 && (
            <ScrollTableRow>
              <ScrollTableCell aria-colspan={3} className="col-span-full">
                Посадки не найдены
              </ScrollTableCell>
            </ScrollTableRow>
          )}
        </ScrollTable>
        <p className="m-0 text-xs" role="status">
          {selected === undefined
            ? 'Выберите посадку в таблице'
            : `Выбрана посадка ${selected + 1}`}
        </p>
      </section>
    </ControlProvider>
  );
};

const meta = {
  title: 'Green Atlas/Surfaces/ScrollTable',
  component: ScrollTableExample,
  args: { initialCount: 100, width: 680 },
  parameters: { layout: 'padded' },
} satisfies Meta<typeof ScrollTableExample>;

export default meta;
type Story = StoryObj<typeof meta>;

export const HundredRows: Story = {};
export const ThreeRows: Story = { args: { initialCount: 3 } };
export const Empty: Story = { args: { initialCount: 0 } };
export const Narrow: Story = { args: { width: 340 } };
