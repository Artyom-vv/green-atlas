import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ZoneConditionsDialog } from './ZoneConditionsDialog';

afterEach(cleanup);
const zone = {
  id: 'z',
  label: 'Двор',
  geometry: {
    type: 'Polygon',
    coordinates: [
      [
        [0, 0],
        [10, 0],
        [10, 10],
        [0, 0],
      ],
    ],
  },
};

describe('ZoneConditionsDialog', () => {
  it('requires an explicit category, regime and basis without assuming a courtyard', async () => {
    const save = vi.fn();
    render(
      <ZoneConditionsDialog
        zone={zone}
        saving={false}
        onSave={save}
        onClose={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('combobox', { name: 'Категория территории' }),
    ).toHaveValue('');
    expect(
      screen.getByRole('combobox', { name: 'Режим территории' }),
    ).toHaveValue('');
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить условия' }));
    expect(save).not.toHaveBeenCalled();
    fireEvent.change(
      screen.getByRole('combobox', { name: 'Категория территории' }),
      { target: { value: 'preschool' } },
    );
    fireEvent.change(
      screen.getByRole('combobox', { name: 'Режим территории' }),
      { target: { value: 'ordinary' } },
    );
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Основание выбора' }),
      { target: { value: '  Проект детского сада  ' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить условия' }));
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith({
        ...zone,
        territory: {
          category: 'preschool',
          regime: 'ordinary',
          basis: 'Проект детского сада',
          spread_control_confirmed: false,
        },
        site_conditions: null,
      }),
    );
  });

  it('preserves saved geometry and observations when only the category changes', async () => {
    const save = vi.fn();
    const saved = {
      ...zone,
      territory: {
        category: 'courtyard' as const,
        regime: 'ordinary' as const,
        basis: 'Проект',
        spread_control_confirmed: false,
      },
      site_conditions: {
        light: 'partial_shade' as const,
        moisture: null,
        drainage: null,
        basis: 'Обследование',
      },
    };
    render(
      <ZoneConditionsDialog
        zone={saved}
        saving={false}
        onSave={save}
        onClose={vi.fn()}
      />,
    );
    fireEvent.change(
      screen.getByRole('combobox', { name: 'Категория территории' }),
      { target: { value: 'major_road' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить условия' }));
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith({
        ...saved,
        territory: {
          ...saved.territory,
          category: 'major_road',
          spread_control_confirmed: false,
        },
      }),
    );
  });
});
