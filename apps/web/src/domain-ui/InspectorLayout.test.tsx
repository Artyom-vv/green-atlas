import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { InspectorBody, InspectorFooter, InspectorSettingRow } from './InspectorLayout';

describe('InspectorLayout', () => {
  it('provides the shared scroll body, semantic footer and setting row contract', () => {
    render(<div>
      <InspectorBody data-testid="body"><section>Содержимое</section></InspectorBody>
      <InspectorSettingRow label="Количество"><button type="button">Изменить</button></InspectorSettingRow>
      <InspectorFooter>Действия</InspectorFooter>
    </div>);

    expect(screen.getByTestId('body')).toHaveClass('inspector-body');
    expect(screen.getByText('Количество').parentElement).toHaveClass('inspector-setting-row');
    expect(screen.getByText('Действия').tagName).toBe('FOOTER');
    expect(screen.getByText('Действия')).toHaveClass('inspector-footer');
  });
});
