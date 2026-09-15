import { fireEvent, render, screen, within } from '@testing-library/react';
import { createRef, type FC, type Ref } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { Button, TextInput } from '../index';
import { ScrollTable } from './ScrollTable';
import {
  ScrollTableCell,
  ScrollTableColumnHeader,
  ScrollTableRow,
} from './ScrollTableParts';

interface TableExampleProps {
  rows: string[];
  onSelect?: (name: string) => void;
  tableRef?: Ref<HTMLDivElement>;
  headerRef?: Ref<HTMLDivElement>;
  inputRef?: Ref<HTMLInputElement>;
  actionRef?: Ref<HTMLButtonElement>;
}

const TableExample: FC<TableExampleProps> = ({
  rows,
  onSelect,
  tableRef,
  headerRef,
  inputRef,
  actionRef,
}) => (
  <ScrollTable
    ref={tableRef}
    aria-label="Посадки"
    columns="1fr 120px"
    minWidth={320}
    header={
      <>
        <ScrollTableColumnHeader ref={headerRef}>
          Название
        </ScrollTableColumnHeader>
        <ScrollTableColumnHeader>Действие</ScrollTableColumnHeader>
      </>
    }
  >
    {rows.map((name, index) => (
      <ScrollTableRow key={name}>
        <ScrollTableCell>
          <TextInput
            ref={index === 0 ? inputRef : undefined}
            aria-label={`Название: ${name}`}
            defaultValue={name}
          />
        </ScrollTableCell>
        <ScrollTableCell>
          <Button
            ref={index === 0 ? actionRef : undefined}
            onClick={() => onSelect?.(name)}
          >
            Показать {name}
          </Button>
        </ScrollTableCell>
      </ScrollTableRow>
    ))}
    {rows.length === 0 && (
      <ScrollTableRow>
        <ScrollTableCell aria-colspan={2}>Посадки не найдены</ScrollTableCell>
      </ScrollTableRow>
    )}
  </ScrollTable>
);

describe('ScrollTable', () => {
  it('exposes one table with its header outside the body scroll viewport', () => {
    render(<TableExample rows={['Липа', 'Клён', 'Дёрен']} />);
    const tables = screen.getAllByRole('table');
    expect(tables).toHaveLength(1);
    expect(tables[0]).toHaveAccessibleName('Посадки');
    const [header, body] = within(tables[0]).getAllByRole('rowgroup');
    expect(within(header).getAllByRole('columnheader')).toHaveLength(2);
    expect(within(header).getAllByRole('row')).toHaveLength(1);
    expect(within(body).getAllByRole('row')).toHaveLength(3);
    expect(within(body).getAllByRole('cell')).toHaveLength(6);
    const viewport = body.querySelector('[data-slot="scroll-viewport"]');
    expect(viewport).toContainElement(within(body).getAllByRole('row')[0]);
    expect(viewport).not.toContainElement(header);
    expect(within(body).queryByRole('columnheader')).not.toBeInTheDocument();
  });

  it('keeps the table and header mounted as rows become empty and return', () => {
    const tableRef = createRef<HTMLDivElement>();
    const headerRef = createRef<HTMLDivElement>();
    const inputRef = createRef<HTMLInputElement>();
    const actionRef = createRef<HTMLButtonElement>();
    const onSelect = vi.fn();
    const props = { tableRef, headerRef, inputRef, actionRef, onSelect };
    const { rerender } = render(<TableExample {...props} rows={['Липа']} />);
    const table = tableRef.current;
    const header = headerRef.current;
    expect(table).toBe(screen.getByRole('table', { name: 'Посадки' }));
    expect(header).toBe(screen.getByRole('columnheader', { name: 'Название' }));
    const input = screen.getByRole('textbox', { name: 'Название: Липа' });
    expect(inputRef.current).toBe(input);
    fireEvent.change(input, { target: { value: 'Черновик названия' } });
    rerender(<TableExample {...props} rows={['Липа', 'Клён']} />);
    expect(inputRef.current).toBe(input);
    expect(input).toHaveValue('Черновик названия');
    expect(actionRef.current).toBe(
      screen.getByRole('button', { name: 'Показать Липа' }),
    );
    fireEvent.click(actionRef.current!);
    expect(onSelect).toHaveBeenLastCalledWith('Липа');

    rerender(<TableExample {...props} rows={[]} />);
    expect(tableRef.current).toBe(table);
    expect(headerRef.current).toBe(header);
    expect(inputRef.current).toBeNull();
    expect(actionRef.current).toBeNull();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(
      screen.getByRole('cell', { name: 'Посадки не найдены' }),
    ).toBeVisible();

    rerender(<TableExample {...props} rows={['Дёрен']} />);
    expect(tableRef.current).toBe(table);
    expect(headerRef.current).toBe(header);
    expect(inputRef.current).toHaveAccessibleName('Название: Дёрен');
    expect(actionRef.current).toHaveAccessibleName('Показать Дёрен');
    fireEvent.click(actionRef.current!);
    expect(onSelect).toHaveBeenLastCalledWith('Дёрен');
  });
});
