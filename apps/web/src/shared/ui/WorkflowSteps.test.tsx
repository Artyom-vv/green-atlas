import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { WorkflowSteps } from '@/shared/ui/WorkflowSteps';

afterEach(cleanup);
it('announces the current step and total without navigation controls', () => {
  render(
    <WorkflowSteps
      labels={['Участки', 'Состав', 'Размещение', 'Проверка']}
      current={2}
      label="Шаги размещения"
    />,
  );
  expect(
    screen.getByRole('status', { name: 'Шаги размещения: 3 из 4, Размещение' }),
  ).toHaveTextContent('Размещение');
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});

it('supports the three-step recommendation route with one current step', () => {
  render(
    <WorkflowSteps
      labels={['Участки', 'Задача', 'Проверка']}
      current={2}
      label="Шаги подбора"
    />,
  );
  expect(
    screen.getByRole('status', { name: 'Шаги подбора: 3 из 3, Проверка' }),
  ).toHaveTextContent('Проверка');
});
