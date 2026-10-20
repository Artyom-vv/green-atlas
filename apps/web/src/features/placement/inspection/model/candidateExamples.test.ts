import { describe, expect, it } from 'vitest';
import type { PatternPreview } from '@green/api-client';
import {
  candidateExamples,
  INSPECTION_EXAMPLE_LIMIT,
} from './candidateExamples';

const sample = (
  x: number,
  code = 'NATIVE_OCCUPIED',
): NonNullable<PatternPreview['skipped']>[number] => ({
  x,
  y: 0,
  code,
  category: 'constraint',
  status: 'blocked',
  reason: 'Препятствие',
});

describe('representative map examples', () => {
  it('omits internal spacing probes and near-duplicate locations', () => {
    const probes = Array.from({ length: 4000 }, (_, i) => sample(i / 100_000));
    probes.push({ ...sample(100), category: 'spacing' });
    expect(candidateExamples(probes, 2)).toHaveLength(1);
  });
  it('shows distant examples and keeps original indices for map inspection', () => {
    const examples = candidateExamples(
      [sample(0), sample(0.01), sample(10), sample(20)],
      2,
    );
    expect(examples.map((e) => e.index)).toEqual([0, 3, 2]);
  });
  it('caps large lists and does not let one frequent cause hide another', () => {
    const examples = candidateExamples(
      Array.from({ length: 100 }, (_, i) => sample(i, `cause-${i}`)),
      2,
    );
    expect(examples).toHaveLength(INSPECTION_EXAMPLE_LIMIT);
    expect(examples[1].candidate.code).toBe('cause-1');
  });
});
