import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ObjectInspector } from '@/widgets/selection-inspector/ui/ObjectInspector';
import type { ValidationIssue } from '@green/api-client';

afterEach(cleanup);

describe('ObjectInspector', () => {
  it('shows one cause for repeated checks of the selected planting', () => {
    const issue = {
      id: 'warning-1',
      object_id: 'plant-1',
      code: 'SOURCE_GEOMETRY_PARTIAL',
      severity: 'warning',
      title: 'Неполная геометрия',
      description: 'Часть исходных объектов недоступна',
    } as ValidationIssue;
    render(
      <ObjectInspector
        object={{
          id: 'plant-1',
          kind: 'shrub',
          x: 1,
          y: 2,
          radius: 1,
          size_class: 'unspecified',
          spacing_policy: 'balanced',
          locked: false,
          status: 'warning',
        }}
        issues={[
          issue,
          { ...issue, id: 'warning-2' },
          { ...issue, id: 'other', object_id: 'plant-2', code: 'OTHER' },
        ]}
        onSpecies={vi.fn()}
        onGrowthHorizon={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('status', { name: 'Ошибки: 0. Замечания: 1.' }),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Что требует внимания (1)' }),
    );
    expect(
      screen.getAllByText('Часть исходных объектов недоступна'),
    ).toHaveLength(1);
  });

  it('keeps a selected planting focused on validation and direct actions', () => {
    render(
      <ObjectInspector
        object={{
          id: 'plant-1',
          kind: 'tree',
          x: 127.41,
          y: 88.29,
          radius: 1.6,
          size_class: 'unspecified',
          spacing_policy: 'balanced',
          locked: false,
          status: 'valid',
        }}
        onSpecies={vi.fn()}
        onGrowthHorizon={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    expect(
      screen.getByRole('heading', { name: 'Дерево', level: 3 }),
    ).toBeInTheDocument();
    expect(screen.getByText('Размещение допустимо')).toBeInTheDocument();
    expect(
      screen.queryByText('Перетащите посадку прямо на карте'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Переместить' }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Удалить' })).toHaveAttribute(
      'data-variant',
      'danger',
    );
    expect(
      screen.getByRole('region', { name: 'Действия с посадкой' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Назначить вид' })).toBeEnabled();
    expect(
      screen.getByRole('region', { name: 'Выбранная посадка' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/Координата|PLAN_OBJECT/),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('Диаметр кроны')).not.toBeInTheDocument();
    expect(screen.getByText(/Без породы: 1 из 1/)).toBeVisible();
    expect(
      screen.getByRole('slider', { name: 'Горизонт прогноза' }),
    ).toBeEnabled();
  });

  it('does not render a fake disabled footer action in read-only mode', () => {
    render(
      <ObjectInspector
        object={{
          id: 'plant-1',
          kind: 'tree',
          x: 127.41,
          y: 88.29,
          radius: 1.6,
          size_class: 'unspecified',
          spacing_policy: 'balanced',
          locked: false,
          status: 'valid',
        }}
        editable={false}
        onSpecies={vi.fn()}
        onGrowthHorizon={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole('button', { name: 'Удалить' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Зафиксировано/)).not.toBeInTheDocument();
    expect(document.querySelector('.inspector-footer')).not.toBeInTheDocument();
  });

  it('identifies a named planting and keeps the 3D move action explicit', () => {
    const onFit = vi.fn(),
      onMove = vi.fn(),
      onSpecies = vi.fn();
    render(
      <ObjectInspector
        object={{
          id: 'plant-1',
          kind: 'tree',
          x: 1,
          y: 2,
          radius: 1.6,
          size_class: 'unspecified',
          spacing_policy: 'balanced',
          locked: false,
          status: 'warning',
        }}
        speciesName="Рябина обыкновенная"
        mapMode="3d"
        onFit={onFit}
        onMove={onMove}
        onSpecies={onSpecies}
        onGrowthHorizon={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('heading', { name: 'Рябина обыкновенная' }),
    ).toBeVisible();
    expect(screen.getByText('Дерево')).toBeVisible();
    expect(screen.getByText('Есть замечания')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'К выделению' }));
    fireEvent.click(screen.getByRole('button', { name: 'Переместить в 2D' }));
    fireEvent.click(screen.getByRole('button', { name: 'Изменить вид' }));
    expect(onFit).toHaveBeenCalledOnce();
    expect(onMove).toHaveBeenCalledOnce();
    expect(onSpecies).toHaveBeenCalledOnce();
    expect(
      screen.queryByRole('slider', { name: 'Горизонт прогноза' }),
    ).not.toBeInTheDocument();
  });

  it('explains a locked planting before its unlock action without exposing edits', () => {
    const onLock = vi.fn();
    render(
      <ObjectInspector
        object={{
          id: 'plant-1',
          kind: 'tree',
          x: 1,
          y: 2,
          radius: 1.6,
          size_class: 'unspecified',
          spacing_policy: 'balanced',
          locked: true,
          status: 'valid',
        }}
        editable={false}
        onLock={onLock}
        onMove={vi.fn()}
        onSpecies={vi.fn()}
        onGrowthHorizon={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    const reason = screen.getByText(
      'Посадка закреплена. Для изменения снимите закрепление.',
    );
    const unlock = screen.getByRole('button', { name: 'Открепить' });
    expect(
      reason.compareDocumentPosition(unlock) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      screen.queryByRole('button', { name: 'Назначить вид' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Переместить' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Удалить' }),
    ).not.toBeInTheDocument();
    fireEvent.click(unlock);
    expect(onLock).toHaveBeenCalledExactlyOnceWith(false);
  });
});
