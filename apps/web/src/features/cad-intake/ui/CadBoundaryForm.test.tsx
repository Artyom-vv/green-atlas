import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CadBoundaryForm } from './CadBoundaryForm';
import {
  boundaryPassport,
  previewRequestFixture,
} from '../test/previewFixtures';

afterEach(cleanup);
describe('authored CAD boundary selection', () => {
  it('requires an available contour and preserves its drawing fingerprints', () => {
    const start = vi.fn();
    render(
      <CadBoundaryForm
        intakeId="intake"
        passport={boundaryPassport}
        disabled={false}
        onStart={start}
      />,
    );
    const button = screen.getByRole('button', {
      name: 'Подготовить предварительную карту',
    });
    expect(button).toBeDisabled();
    expect(
      screen.getByRole('option', { name: /Наклонная плоскость/ }),
    ).toBeDisabled();
    fireEvent.change(
      screen.getByRole('combobox', { name: 'Авторский контур' }),
      { target: { value: '14651AD' } },
    );
    fireEvent.click(button);
    expect(start).toHaveBeenCalledWith(previewRequestFixture);
  });
  it('distinguishes a legacy passport from a drawing without closed contours', () => {
    const legacy = structuredClone(boundaryPassport);
    legacy.drawings[0].inspection!.boundary_catalog = null;
    const { rerender } = render(
      <CadBoundaryForm
        intakeId="intake"
        passport={legacy}
        disabled={false}
        onStart={vi.fn()}
      />,
    );
    expect(
      screen.getByText('Для списка контуров повторите проверку комплекта.'),
    ).toBeVisible();
    legacy.drawings[0].inspection!.boundary_catalog = {
      schema_version: 'green-atlas-boundary-catalog-v1',
      truncated: false,
      total_candidates: 0,
      candidates: [],
    };
    rerender(
      <CadBoundaryForm
        intakeId="intake"
        passport={legacy}
        disabled={false}
        onStart={vi.fn()}
      />,
    );
    expect(screen.getByText(/замкнутые контуры не найдены/)).toBeVisible();
  });
});
