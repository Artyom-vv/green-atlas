import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { preparationApi } from '@/features/source-preparation/api/preparationApi';
import { useState } from 'react';
import { NativeAreaReview } from './NativeAreaReview';
import type { AreaProposal } from './nativeAreaPresentation';

const proposal: AreaProposal = {
  id: 'area-proposal/78B3',
  source: { handle: '78B3', instance_chain: [] },
  layer: 'Здания',
  source_path_content_sha256: 'a'.repeat(64),
  proposal_sha256: 'b'.repeat(64),
  closure_gap_m: 0.0145,
  area_m2: 1096.77,
  area_gap_entity_type: 'LWPOLYLINE',
  decision: 'pending',
};
const preview = {
  source_sha256: 'c'.repeat(64),
  proposal_sha256: proposal.proposal_sha256,
  source_path: [
    [0, 0],
    [10, 0],
    [10, 10],
    [0, 10],
    [0, 0.0145],
  ] as [number, number][],
  proposed_rings: [
    [
      [0, 0],
      [10, 0],
      [10, 10],
      [0, 10],
      [0, 0],
    ],
  ] as [number, number][][],
};
function open(readOnly = false) {
  const onDecision = vi.fn();
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <NativeAreaReview
        projectId="project"
        sourceSha256={preview.source_sha256}
        proposals={[proposal]}
        readOnly={readOnly}
        busy={false}
        onDecision={onDecision}
      />
    </QueryClientProvider>,
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Замыкание контуров (1 на проверке)' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Показать контур' }));
  return onDecision;
}

describe('NativeAreaReview', () => {
  it('does not demand individual confirmation when native assembly is automatic', () => {
    render(<NativeAreaReview projectId="project" sourceSha256={preview.source_sha256}
      proposals={[proposal]} readOnly={false} busy={false} automatic onDecision={vi.fn()} />);
    expect(screen.getByRole('button', {name: 'Ручная корректировка замыканий'})).toBeInTheDocument();
    expect(screen.queryByRole('button', {name: /на проверке/})).not.toBeInTheDocument();
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });
  it('requires seeing a same-capture contour before accepting it', async () => {
    vi.spyOn(preparationApi, 'getNativeAreaPreview').mockResolvedValue(preview);
    const onDecision = open();
    expect(onDecision).not.toHaveBeenCalled();
    await screen.findByRole('img', {
      name: 'Исходная линия и площадь после замыкания',
    });
    fireEvent.click(screen.getByRole('button', { name: 'Место разрыва' }));
    expect(
      screen.getByRole('img', {
        name: 'Разрыв исходной линии и предлагаемое замыкание',
      }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Принять замыкание' }));
    expect(onDecision).toHaveBeenCalledWith(proposal, 'accepted');
  });
  it('lets a read-only project inspect but not change the contour', async () => {
    vi.spyOn(preparationApi, 'getNativeAreaPreview').mockResolvedValue(preview);
    open(true);
    await screen.findByRole('img');
    expect(
      screen.getByRole('button', { name: 'Принять замыкание' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Отклонить замыкание' }),
    ).toBeDisabled();
  });
  it('does not accept a preview from another capture', async () => {
    vi.spyOn(preparationApi, 'getNativeAreaPreview').mockResolvedValue({
      ...preview,
      source_sha256: 'd'.repeat(64),
    });
    open();
    await screen.findByText('Не удалось сверить контур с исходником');
    expect(
      screen.getByRole('button', { name: 'Принять замыкание' }),
    ).toBeDisabled();
  });
  it('renders tiny endpoint gaps in local coordinates without changing source data', async () => {
    const offset = ([x, y]: [number, number]): [number, number] => [
      x + 15919,
      y - 5089,
    ];
    const shifted = {
      ...preview,
      source_path: preview.source_path.map(offset),
      proposed_rings: preview.proposed_rings.map((ring) => ring.map(offset)),
    };
    const original = JSON.stringify(shifted);
    vi.spyOn(preparationApi, 'getNativeAreaPreview').mockResolvedValue(shifted);
    open();
    await screen.findByRole('img');
    fireEvent.click(screen.getByRole('button', { name: 'Место разрыва' }));
    const svg = screen.getByRole('img');
    const markers = [...svg.querySelectorAll('circle')];
    expect(markers).toHaveLength(2);
    for (const marker of markers) {
      expect(Math.abs(Number(marker.getAttribute('cx')))).toBeLessThan(0.02);
      expect(Math.abs(Number(marker.getAttribute('cy')))).toBeLessThan(0.02);
    }
    expect(
      Math.abs(
        Number(markers[0].getAttribute('cy')) -
          Number(markers[1].getAttribute('cy')),
      ),
    ).toBeCloseTo(0.0145, 8);
    expect(JSON.stringify(shifted)).toBe(original);
  });
  it('shows loading before admission and keeps the contour visible while saving', async () => {
    let finishPreview!: (value: typeof preview) => void;
    vi.spyOn(preparationApi, 'getNativeAreaPreview').mockReturnValue(
      new Promise((resolve) => {
        finishPreview = resolve;
      }),
    );
    function PendingSave() {
      const [saving, setSaving] = useState(false);
      return (
        <NativeAreaReview
          projectId="project"
          sourceSha256={preview.source_sha256}
          proposals={[proposal]}
          readOnly={false}
          busy={saving}
          saving={saving}
          onDecision={() => setSaving(true)}
        />
      );
    }
    render(
      <QueryClientProvider client={new QueryClient()}>
        <PendingSave />
      </QueryClientProvider>,
    );
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Замыкание контуров (1 на проверке)',
      }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Показать контур' }));
    expect(screen.getByText('Проверяем контур по исходнику')).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Принять замыкание' }),
    ).toBeDisabled();
    finishPreview(preview);
    await screen.findByRole('img');
    fireEvent.click(screen.getByRole('button', { name: 'Принять замыкание' }));
    expect(screen.getByText('Сохраняем решение')).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Отклонить замыкание' }),
    ).toBeDisabled();
    fireEvent.click(screen.getAllByRole('button', { name: 'Закрыть' })[0]);
    expect(screen.getByRole('dialog')).toBeVisible();
    expect(screen.getByRole('img')).toBeVisible();
  });
});
