import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RecommendationReviewPanel } from './RecommendationReviewPanel';

afterEach(cleanup);

describe('RecommendationReviewPanel', () => {
  it('does not turn absent evidence into an ecological score', () => {
    render(<RecommendationReviewPanel proposal={{
      profile: 'balanced',
      arrangement: 'area',
      evidence: { spatial_constraints: 'partial', species_catalog: 'verified', sunlight: 'missing', soil: 'missing', hydrology: 'missing', note: 'Проверена геометрия.' },
      change_set: undefined,
      explanations: [],
      skipped: [],
      data_gaps: ['Инсоляция', 'Почва'],
    }} onApply={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText('В исходных данных есть пробелы.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Что учтено' }));
    expect(screen.getByText('Инсоляция, Почва.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Добавить 0' })).toBeDisabled();
    expect(screen.queryByText(/балл|процент/i)).not.toBeInTheDocument();
  });
});
