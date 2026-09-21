import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import type { RecommendationPreview } from '@green/api-client';
import { RecommendationZoneEvidence } from './RecommendationZoneEvidence';

afterEach(cleanup);

it('keeps zone categories, exclusions and missing site evidence distinct', () => {
  const proposal: RecommendationPreview = {
    profile: 'balanced',
    arrangement: 'area',
    explanations: [],
    skipped: [],
    evidence: {
      spatial_constraints: 'partial',
      species_catalog: 'partial',
      sunlight: 'missing',
      soil: 'missing',
      hydrology: 'missing',
      note: 'Проверка',
    },
    zone_results: [
      {
        zone_id: 'west',
        territory: {
          category: 'healthcare',
          regime: 'ordinary',
          spread_control_confirmed: false,
          basis: 'Медицинское учреждение',
        },
        requested_count: 2,
        accepted_count: 0,
        species_options: [
          {
            species_revision_id: 'birch',
            assortment_status: 'not_recommended',
            accepted_count: 0,
            crown_projection_sum_m2: 0,
            source_url: 'https://example.org/table.pdf',
            source_page: 2,
            source_row: 7,
          },
        ],
      },
      {
        zone_id: 'east',
        territory: {
          category: 'preschool',
          regime: 'ordinary',
          spread_control_confirmed: false,
          basis: 'Детский сад',
        },
        requested_count: 2,
        accepted_count: 0,
        species_options: [
          {
            species_revision_id: 'birch',
            assortment_status: 'listed',
            accepted_count: 0,
            crown_projection_sum_m2: 0,
            site_suitability: {
              status: 'unknown',
              checks: [
                {
                  dimension: 'moisture',
                  status: 'unknown',
                  reason: 'Влажность не подтверждена',
                },
              ],
            },
          },
        ],
      },
    ],
  };
  render(
    <RecommendationZoneEvidence
      proposal={proposal}
      speciesNames={new Map([['birch', 'Берёза']])}
      zoneNames={
        new Map([
          ['west', 'Запад'],
          ['east', 'Восток'],
        ])
      }
    />,
  );
  const west = screen.getByRole('heading', { name: 'Запад' }).parentElement!;
  const east = screen.getByRole('heading', { name: 'Восток' }).parentElement!;
  expect(west).toHaveTextContent('Медицинские учреждения. Подобрано 0 из 2.');
  expect(west).toHaveTextContent('Не рекомендовано для этой территории');
  expect(
    within(west).getByText('Таблица Москвы, стр. 2, строка 7'),
  ).toHaveAttribute('href', 'https://example.org/table.pdf#page=2');
  expect(east).toHaveTextContent('Детские сады. Подобрано 0 из 2.');
  expect(east).toHaveTextContent('Влажность не подтверждена');
  expect(east).not.toHaveTextContent('Не рекомендовано');
  expect(east).not.toHaveTextContent('Допустимых мест:');
});
