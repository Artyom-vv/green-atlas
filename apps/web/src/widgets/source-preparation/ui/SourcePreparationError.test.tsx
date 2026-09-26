import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import type { Layer } from '@green/api-client';
import { sourceMappingErrorFixture } from '@/test/sourcePreparationErrorFixture';
import { SourcePreparationError } from './SourcePreparationError';

afterEach(cleanup);
it('shows the failing layer name and the server validation reason', () => {
  render(<SourcePreparationError
    error={sourceMappingErrorFixture()}
    mappings={{ border: { layer_id: 'border', kind: 'ignore', visible: true } }}
    layers={[{ id: 'border', source_name: 'Подоснова|Граница работ' } as Layer]} />);
  expect(screen.getByText(/Подоснова\|Граница работ: Категория не соответствует/)).toBeVisible();
  expect(screen.getByText(/Выбранные значения сохранены в форме/)).toBeVisible();
});
it('preserves an actionable server error without field details', () => {
  render(<SourcePreparationError error={new Error('Связь с чертежом устарела — обновите исходные данные')}
    layers={[]} mappings={{}} />);
  expect(screen.getByText('Связь с чертежом устарела — обновите исходные данные')).toBeVisible();
});
