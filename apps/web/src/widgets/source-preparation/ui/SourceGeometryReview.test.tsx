import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Layer, Project } from '@green/api-client';
import {
  SourceGeometryReview,
  type SourceGeometryReviewProps,
} from './SourceGeometryReview';

const layer: Layer = {
  id: 'work-boundary',
  source_name: 'Генплан|Граница работ',
  mapped_kind: 'site_border',
  suggested_kind: 'site_border',
  object_count: 1,
  color: '#475569',
  linetype: 'CONTINUOUS',
  geometry_complete: true,
  required: false,
  visible: true,
  boundary_candidate: {
    status: 'usable',
    basis: 'polygonized_linework',
    area_m2: 76625,
    inset_1_5m_area_m2: 76000,
    component_count: 1,
  },
};

function setup(changes: Partial<SourceGeometryReviewProps> = {}) {
  const project: Project = {
    id: 'project',
    import_status: { mode: 'autocad_live' },
    source_file: {
      content_sha256: 'a'.repeat(64),
      cad_snapshot_provenance: { live_capture: true },
    },
  } as unknown as Project;
  const props: SourceGeometryReviewProps = {
    sourceWarnings: [],
    incompleteConstraintLayers: [],
    partialAccepted: false,
    hasPlanningBoundary: true,
    reviewOnly: false,
    preparationBlocked: false,
    layers: [layer],
    mappings: {
      [layer.id]: {
        layer_id: layer.id,
        kind: 'site_border',
        confirmed: true,
        visible: true,
      },
    },
    setMappings: vi.fn(),
    projectQuery: {
      data: project,
      refetch: vi.fn(),
    } as unknown as SourceGeometryReviewProps['projectQuery'],
    nativeAreaProposals: [],
    decideNativeArea: {
      isPending: false,
      error: null,
      mutate: vi.fn(),
      reset: vi.fn(),
    } as unknown as SourceGeometryReviewProps['decideNativeArea'],
    boundaryOpen: false,
    onBoundaryOpenChange: vi.fn(),
    ...changes,
  };
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SourceGeometryReview {...props} />
    </QueryClientProvider>,
  );
  return props;
}

describe('SourceGeometryReview', () => {
  afterEach(cleanup);

  it('keeps geometry gaps visible while putting their addresses behind the same task', () => {
    const incomplete = {
      ...layer,
      id: 'building',
      source_name: 'Топография|Здания',
      geometry_complete: false,
      mapped_kind: 'building' as const,
    };
    const props = setup({ incompleteConstraintLayers: [incomplete] });
    const gaps = screen.getByRole('button', {
      name: 'Часть геометрии недоступна (1)',
    });
    expect(screen.getByText(incomplete.source_name)).not.toBeVisible();
    expect(props.setMappings).not.toHaveBeenCalled();
    fireEvent.click(gaps);
    expect(screen.getByText(incomplete.source_name)).toBeVisible();
    expect(
      screen.getByText('Пропуски остаются в отчёте и не участвуют в проверках'),
    ).toBeVisible();
    expect(props.decideNativeArea.mutate).not.toHaveBeenCalled();
  });

  it('keeps file diagnostics in the source completeness group', () => {
    setup({ sourceWarnings: ['XREF 12A74 не извлечён'] });
    const group = screen.getByRole('region', { name: 'Полнота исходника' });
    expect(
      within(group).getByRole('button', { name: 'Замечания к файлу (1)' }),
    ).toBeVisible();
    expect(
      screen
        .getByRole('button', { name: 'Граница расчёта' })
        .closest('section'),
    ).not.toBe(group);
  });

  it('does not render empty task groups for a legacy read-only source', () => {
    setup({
      reviewOnly: true,
      projectQuery: {
        data: { id: 'legacy', source_file: {} },
        refetch: vi.fn(),
      } as unknown as SourceGeometryReviewProps['projectQuery'],
    });
    expect(
      screen.queryByRole('region', { name: 'Геометрия объектов' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('region', { name: 'Полнота исходника' }),
    ).not.toBeInTheDocument();
  });

  it('does not change a selected boundary or attest incomplete geometry by opening its task', () => {
    const props = setup({ boundaryOpen: true });
    expect(screen.getByRole('radio', { name: /Граница работ/ })).toBeChecked();
    expect(props.setMappings).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Граница расчёта' }));
    expect(props.onBoundaryOpenChange).toHaveBeenCalledWith(false);
    expect(props.setMappings).not.toHaveBeenCalled();
  });

  it('retains the full AutoCAD warning as contextual detail without demanding another decision', () => {
    const warning = 'XREF 03_Проектные решения: объект 12A74 не извлечён';
    setup({ sourceWarnings: [warning] });
    expect(screen.getByText(warning)).not.toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Замечания к файлу (1)' }),
    );
    expect(screen.getByText(warning)).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Объекты без расчётной геометрии' }),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Замечания к файлу (1)' }),
    );
    expect(screen.getByText(warning)).not.toBeVisible();
  });

  it('opens boundary help from its heading without opening or changing the boundary', async () => {
    const props = setup();
    fireEvent.click(screen.getByRole('button', { name: 'О выборе границы' }));
    expect(
      await screen.findByText(
        'Граница задаёт предел расчёта. Места посадки выбираются на карте',
      ),
    ).toBeVisible();
    expect(props.onBoundaryOpenChange).not.toHaveBeenCalled();
    expect(props.setMappings).not.toHaveBeenCalled();
    expect(
      screen.getByRole('button', { name: 'Граница расчёта' }),
    ).toHaveAttribute('aria-expanded', 'false');
  });
});
