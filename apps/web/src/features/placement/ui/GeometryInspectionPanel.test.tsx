import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { GeometryInspectionPanel } from './GeometryInspectionPanel';
import type { CandidateInspection } from '../model/useCandidateInspection';

afterEach(cleanup);

describe('geometry inspection', () => {
  it('keeps technical causes behind details and exposes measured distances', () => {
    const startExplaining = vi.fn();
    render(<GeometryInspectionPanel inspection={{ canExplain: true, diagnosing: true, startExplaining,
      diagnosis: { status: 'unknown', reason: 'Нужна проверка', geometry_evidence: {
        state: 'unknown', radius_m: 1.6, canopy_radius_m: 3, root_radius_m: 3.6, unlocated_objects: 0,
        causes: [{ message: 'Недостаточный отступ', stage: 'clearance', source_layer: 'Сети',
          measured_distance_m: 2, required_distance_m: 3.6, action: 'Выбрать другую точку',
          code: 'clearance', query_sent: true, source_feature_ids: ['A/B'] }],
      } },
    } as unknown as CandidateInspection} />);
    fireEvent.click(screen.getByRole('button', { name: 'Проверить точку на карте' }));
    expect(startExplaining).toHaveBeenCalled();
    fireEvent.click(screen.getByText('Недостаточный отступ'));
    expect(screen.getByText('Измерено 2 м')).toBeVisible();
    expect(screen.getByText('Требуется 3,6 м')).toBeVisible();
    fireEvent.click(screen.getByText('Технические сведения'));
    expect(screen.getByText('A/B')).toBeVisible();
    expect(screen.getByText('Передан в AutoCAD')).toBeVisible();
  });

  it('does not claim missing evidence is a successful geometry check', () => {
    render(<GeometryInspectionPanel inspection={{ canExplain: true, diagnosis: { status: 'unknown', reason: 'Не проверено' } } as CandidateInspection} />);
    expect(screen.getByText('Подробности геометрической проверки недоступны')).toBeVisible();
    expect(screen.queryByText('В этой точке геометрия не запрещает посадку')).toBeNull();
  });

  it('explains locally prepared geometry without implying a missing CAD request', () => {
    render(<GeometryInspectionPanel inspection={{ canExplain: true, diagnosis: {
      status: 'unknown', reason: 'Проверить контур', geometry_evidence: {
        measurement_backend: 'prepared_geometry', state: 'unknown', radius_m: 1,
        canopy_radius_m: 1, root_radius_m: 1, unlocated_objects: 0,
        causes: [{ message: 'Нет подготовленной геометрии', stage: 'area',
          action: 'Обновить подготовку', code: 'prepared_geometry_missing', query_sent: false,
          source_feature_ids: ['A/B'] }],
      },
    } } as unknown as CandidateInspection} />);
    fireEvent.click(screen.getByText('Нет подготовленной геометрии'));
    fireEvent.click(screen.getByText('Технические сведения'));
    expect(screen.getByText('По сохранённой геометрии, без запроса в AutoCAD')).toBeVisible();
    expect(screen.queryByText('Не передан в AutoCAD')).not.toBeInTheDocument();
  });
});
