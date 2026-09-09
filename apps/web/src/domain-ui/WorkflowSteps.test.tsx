import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { WorkflowSteps } from './WorkflowSteps';

afterEach(cleanup);
it('distinguishes completed, current and upcoming steps without navigation controls', () => {
  render(<WorkflowSteps labels={['Участки', 'Состав', 'Размещение', 'Проверка']} current={2} label="Шаги размещения" />);
  const steps = screen.getAllByRole('listitem');
  expect(steps.map(step => step.dataset.state)).toEqual(['complete', 'complete', 'current', 'upcoming']);
  expect(screen.getByLabelText('3. Размещение, текущий шаг')).toHaveAttribute('aria-current', 'step');
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});

it('supports the three-step recommendation route with one current step', () => {
  render(<WorkflowSteps labels={['Участки', 'Задача', 'Проверка']} current={2} label="Шаги подбора" />);
  expect(screen.getAllByRole('listitem')).toHaveLength(3);
  expect(screen.getByLabelText('3. Проверка, текущий шаг')).toHaveAttribute('aria-current', 'step');
});
