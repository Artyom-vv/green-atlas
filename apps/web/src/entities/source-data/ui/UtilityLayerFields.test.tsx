import { useState } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { LayerMapping } from '@green/api-client';
import { UtilityLayerFields } from './UtilityLayerFields';

const mapping: LayerMapping = {
  layer_id: 'network',
  kind: 'utility',
  category: 'water_network',
  confirmed: true,
  visible: true,
  utility_context: {
    network_type: 'water',
    installation: 'underground',
    geometry_reference: 'axis',
    source_reference: 'Паспорт сети',
    confirmed_by: 'Проектировщик',
    review_status: 'unconfirmed',
  },
  utility_axis_bindings: [],
};
afterEach(cleanup);

it('requires an explicit attestation for complete embedded network fields and resets it after edits', () => {
  const onChange = vi.fn();
  function Harness() {
    const [draft, setDraft] = useState(mapping);
    return (
      <UtilityLayerFields
        embedded
        mapping={draft}
        onChange={(next) => {
          onChange(next);
          setDraft(next);
        }}
      />
    );
  }
  render(<Harness />);
  expect(
    screen.queryByRole('button', { name: 'Подтвердить параметры' }),
  ).toBeNull();
  const checkbox = screen.getByRole('checkbox', {
    name: 'Подтверждаю параметры сети по указанному источнику',
  });
  expect(checkbox).not.toBeChecked();
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.click(checkbox);
  expect(onChange.mock.calls[0][0]).toMatchObject({
    confirmed: true,
    utility_context: { review_status: 'confirmed' },
    utility_axis_bindings: [],
  });
  expect(checkbox).toBeChecked();
  fireEvent.change(screen.getByLabelText('Кто подтвердил'), {
    target: { value: 'Другой проектировщик' },
  });
  expect(checkbox).not.toBeChecked();
  expect(onChange.mock.calls[1][0]).toMatchObject({
    confirmed: true,
    utility_context: {
      review_status: 'unconfirmed',
      confirmed_by: 'Другой проектировщик',
    },
  });
});

it('does not allow attestation before all source and network facts are provided', () => {
  render(
    <UtilityLayerFields
      embedded
      mapping={{
        ...mapping,
        utility_context: { ...mapping.utility_context!, source_reference: '' },
      }}
      onChange={() => {}}
    />,
  );
  expect(screen.queryByRole('checkbox')).toBeNull();
  expect(screen.getByLabelText('Источник сведений')).toBeVisible();
});

it('keeps legacy disclosure and its explicit confirmation action', () => {
  const onChange = vi.fn();
  render(<UtilityLayerFields mapping={mapping} onChange={onChange} />);
  fireEvent.click(
    screen.getByRole('button', { name: 'Дополнительные сведения' }),
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Подтвердить параметры' }),
  );
  expect(onChange).toHaveBeenCalledWith({
    ...mapping,
    utility_context: { ...mapping.utility_context, review_status: 'confirmed' },
  });
});

it('keeps readonly facts inspectable without allowing attestation', () => {
  render(
    <UtilityLayerFields
      embedded
      readOnly
      mapping={mapping}
      onChange={() => {}}
    />,
  );
  expect(screen.getByRole('combobox', { name: 'Тип сети' })).toBeDisabled();
  expect(screen.getByRole('checkbox')).toBeDisabled();
  expect(screen.getByLabelText('Источник сведений')).toHaveAttribute(
    'readonly',
  );
});
