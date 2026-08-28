import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RecommendationReviewPanel } from './RecommendationReviewPanel';

afterEach(cleanup);

describe('RecommendationReviewPanel', () => {
  it('does not turn absent evidence into an ecological score', () => {
    render(<RecommendationReviewPanel proposal={{
      profile: 'balanced',
      evidence: { spatial_constraints: 'partial', species_catalog: 'verified', sunlight: 'missing', soil: 'missing', hydrology: 'missing', note: 'Проверена геометрия.' },
      change_set: undefined,
      explanations: [],
      skipped: [],
      data_gaps: ['Инсоляция', 'Почва'],
    }} onApply={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText('Инсоляция, Почва.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Применить' })).toBeDisabled();
    expect(screen.queryByText(/балл|процент/i)).not.toBeInTheDocument();
  });
});
