import { expect, it } from 'vitest';
import type { PatternPreview } from '@green/api-client';
import {
  patternResultReasons,
  patternSearchNotice,
} from './patternResultPresentation';

const preview: PatternPreview = {
  planning_horizon_year: 0,
  pattern_id: 'bounded-search',
  type: 'fill',
  requested_count: 40,
  generated_count: 5000,
  accepted_count: 0,
  rejected_count: 40,
  capacity_shortfall: 0,
  data_confidence: 'limited',
  skipped: [],
};

it('объединяет отказы у сохранённых и новых посадок без потери других ограничений', () => {
  const reasons = patternResultReasons({
    ...preview,
    reason_summary: [
      {
        status: 'blocked',
        code: 'PLANT_SPACING',
        category: 'spacing',
        count: 4800,
        message: 'Слишком близко к существующим посадкам',
      },
      {
        status: 'blocked',
        code: 'PLANT_SPACING',
        category: 'spacing',
        count: 200,
        message: 'Слишком близко к принятой посадке',
      },
      {
        status: 'blocked',
        code: 'NATIVE_CLEARANCE',
        category: 'constraint',
        count: 2,
        message: 'Недостаточный отступ от здания',
      },
    ],
  });
  expect(reasons).toHaveLength(2);
  expect(
    reasons.find(
      (reason) =>
        reason.message === 'Недостаточно расстояния до других посадок',
    )?.count,
  ).toBe(5000);
  expect(
    reasons.find(
      (reason) => reason.message === 'Недостаточный отступ от препятствий',
    )?.count,
  ).toBe(2);
});

it('не превращает неопределённость расстояния в доказанный запрет', () => {
  const reasons = patternResultReasons({
    ...preview,
    reason_summary: [
      {
        status: 'unknown',
        code: 'SPACING_UNKNOWN',
        category: 'spacing',
        count: 3,
        message: 'Не удалось подтвердить расстояние до посадок',
      },
    ],
  });
  expect(reasons).toHaveLength(1);
  expect(reasons[0].message).toBe(
    'Не удалось подтвердить расстояние до посадок',
  );
  expect(reasons[0].key).toContain('unknown');
});

it('не выдаёт незавершённую подготовку области за исчерпание поиска', () => {
  expect(
    patternSearchNotice({
      ...preview,
      search_stop_reason: 'domain_exhausted',
      search_domains: [
        {
          zone_id: 'work',
          revision: 'pending',
          method: 'hybrid',
          final_check: 'prepared_geometry',
          geometry: {},
          unresolved_geometry: {},
          available_area_m2: 0,
          excluded_area_m2: 0,
          unresolved_area_m2: 0,
          pending_area_m2: 100,
          measured_cells: 0,
          processed_objects: 0,
          total_objects: 10,
          cache_hits: 0,
          elapsed_s: 0,
          stop_reason: 'time_limit',
        },
      ],
    }),
  ).toBeUndefined();
});
