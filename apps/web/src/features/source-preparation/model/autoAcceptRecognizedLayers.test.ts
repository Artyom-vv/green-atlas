import { describe, expect, it } from 'vitest';
import type { Layer, LayerMapping, LayerRecognition } from '@green/api-client';
import { autoAcceptRecognizedLayers } from './autoAcceptRecognizedLayers';

const layer = (id: string): Layer => ({
  id,
  source_name: id,
  suggested_kind: 'ignore',
  mapped_kind: 'ignore',
  color: '#000',
  object_count: 1,
  geometry_complete: true,
  linetype: 'CONTINUOUS',
  required: false,
  visible: true,
  mapping_review_required: true,
  mapping_confirmed: false,
});

const mapping = (id: string): LayerMapping => ({
  layer_id: id,
  kind: 'ignore',
  confirmed: false,
  visible: true,
});

describe('automatic layer acceptance', () => {
  it('accepts decisive roles and exclusions, leaving uncertain and boundary layers pending', () => {
    const ids = [
      'building',
      'annotation',
      'sidewalk',
      'border',
      'mixed',
      'accepted',
      'mediumUtility',
      'lowUtility',
      'mediumAnnotation',
    ];
    const mappings = Object.fromEntries(ids.map((id) => [id, mapping(id)]));
    mappings.accepted = {
      layer_id: 'accepted',
      kind: 'road',
      confirmed: true,
      visible: true,
    };
    const recognition: LayerRecognition = {
      source_sha256: 'source',
      provider: 'openai/gpt-6-luna',
      status: 'completed',
      processed_count: ids.length,
      total_count: ids.length,
      categories: [
        { category: 'building', kind: 'building', label: 'Здание' },
        { category: 'annotation', kind: 'ignore', label: 'Подписи' },
        { category: 'footway', kind: 'road', label: 'Тротуар' },
        { category: 'project_boundary', kind: 'site_border', label: 'Граница' },
        { category: 'mixed_source', kind: null, label: 'Смешанный' },
        { category: 'power_network', kind: 'utility', label: 'Кабель' },
        { category: 'unspecified_network', kind: 'utility', label: 'Сеть' },
      ],
      proposals: [
        {
          layer_id: 'building',
          category: 'building',
          confidence: 'high',
          evidence: [],
          unresolved: [],
        },
        {
          layer_id: 'annotation',
          category: 'annotation',
          confidence: 'high',
          evidence: [],
          unresolved: [],
        },
        {
          layer_id: 'sidewalk',
          category: 'footway',
          confidence: 'medium',
          evidence: [],
          unresolved: [],
        },
        {
          layer_id: 'border',
          category: 'project_boundary',
          confidence: 'high',
          evidence: [],
          unresolved: [],
        },
        {
          layer_id: 'mixed',
          category: 'mixed_source',
          confidence: 'high',
          evidence: [],
          unresolved: [],
        },
        {
          layer_id: 'accepted',
          category: 'building',
          confidence: 'high',
          evidence: [],
          unresolved: [],
        },
        {
          layer_id: 'mediumUtility',
          category: 'power_network',
          confidence: 'medium',
          evidence: [],
          unresolved: ['Тип не подтверждён'],
        },
        {
          layer_id: 'lowUtility',
          category: 'unspecified_network',
          confidence: 'low',
          evidence: [],
          unresolved: ['Тип сети не указан'],
        },
        {
          layer_id: 'mediumAnnotation',
          category: 'annotation',
          confidence: 'medium',
          evidence: [],
          unresolved: ['Смысл неясен'],
        },
      ],
    };
    const result = autoAcceptRecognizedLayers(
      ids.map(layer),
      mappings,
      recognition,
    );
    expect(result.building).toMatchObject({
      kind: 'building',
      category: 'building',
      confirmed: true,
    });
    expect(result.annotation).toMatchObject({
      kind: 'ignore',
      category: 'annotation',
      confirmed: true,
    });
    expect(result.sidewalk.confirmed).toBe(true);
    expect(result.border.confirmed).toBe(false);
    expect(result.mixed.confirmed).toBe(false);
    expect(result.mediumUtility).toMatchObject({
      kind: 'utility',
      confirmed: true,
    });
    expect(result.lowUtility).toMatchObject({
      kind: 'utility',
      confirmed: true,
    });
    expect(result.mediumAnnotation.confirmed).toBe(false);
    expect(result.accepted).toBe(mappings.accepted);
    expect(mappings.building.confirmed).toBe(false);
  });

  it('does not accept unfinished recognition', () => {
    const mappings = { building: mapping('building') };
    expect(autoAcceptRecognizedLayers([layer('building')], mappings)).toBe(
      mappings,
    );
  });

  it('does not hide a physical line even if the model calls it an annotation', () => {
    const source = {
      ...layer('questions'), suggested_kind: 'utility' as const,
      entity_types: { 'AUTOCAD:AcDbMLeader': 1, LWPOLYLINE: 1 },
    };
    const result = autoAcceptRecognizedLayers(
      [source], { questions: mapping('questions') },
      {
        source_sha256: 'source', provider: 'openai/gpt-6-luna',
        status: 'completed', processed_count: 1, total_count: 1,
        categories: [{ category: 'annotation', kind: 'ignore', label: 'Подписи' }],
        proposals: [{ layer_id: 'questions', category: 'annotation',
          confidence: 'high', evidence: [], unresolved: [] }],
      },
    );
    expect(result.questions.confirmed).toBe(false);
  });

  it('accepts a medium-confidence review marker made only of callout circles', () => {
    const source = {
      ...layer('questions'), source_name: 'Рецензия|Вопросы',
      entity_types: { CIRCLE: 5 },
    };
    const result = autoAcceptRecognizedLayers(
      [source], { questions: mapping('questions') },
      {
        source_sha256: 'source', provider: 'openai/gpt-6-luna',
        status: 'completed', processed_count: 1, total_count: 1,
        categories: [{ category: 'annotation', kind: 'ignore', label: 'Аннотация' }],
        proposals: [{ layer_id: 'questions', category: 'annotation',
          confidence: 'medium', evidence: [], unresolved: [] }],
      },
    );
    expect(result.questions).toMatchObject({
      kind: 'ignore', category: 'annotation', confirmed: true,
    });
  });

  it('uses one complete work contour ahead of a wider topographic order extent', () => {
    const work = {
      ...layer('work'), source_name: 'Границы работ|Граница работ',
      boundary_candidate: { status: 'usable' as const, basis: 'source_surface',
        area_m2: 76000, inset_1_5m_area_m2: 72000, component_count: 1 },
    };
    const order = {
      ...layer('order'), source_name: 'Топография|Граница заказа',
      boundary_candidate: { status: 'usable' as const, basis: 'source_surface',
        area_m2: 168000, inset_1_5m_area_m2: 164000, component_count: 1 },
    };
    const recognition: LayerRecognition = {
      source_sha256: 'source', provider: 'openai/gpt-6-luna', status: 'completed',
      processed_count: 2, total_count: 2, categories: [],
      proposals: [
        { layer_id: 'work', category: 'project_boundary', confidence: 'high', evidence: [], unresolved: [] },
        { layer_id: 'order', category: 'unspecified_topography', confidence: 'medium', evidence: [], unresolved: [] },
      ],
    };
    const result = autoAcceptRecognizedLayers(
      [work, order], { work: mapping('work'), order: mapping('order') }, recognition,
    );
    expect(result.work).toMatchObject({ kind: 'site_border', category: 'project_boundary', confirmed: true });
    expect(result.order).toMatchObject({ kind: 'ignore', category: 'survey_reference', confirmed: true });
  });

  it('sets aside an unusable alternative work line when one work contour is valid', () => {
    const work = {
      ...layer('work'), source_name: 'Границы работ|Граница работ',
      boundary_candidate: { status: 'usable' as const, basis: 'source_surface',
        area_m2: 76000, inset_1_5m_area_m2: 72000, component_count: 1 },
    };
    const openLine = {
      ...layer('open'), source_name: 'Генплан|Граница проектирования',
      boundary_candidate: { status: 'unavailable' as const, basis: 'polygonized_linework',
        area_m2: 0, inset_1_5m_area_m2: 0, component_count: 0,
        issue: 'Нет замкнутой поверхности' },
    };
    const recognition: LayerRecognition = {
      source_sha256: 'source', provider: 'openai/gpt-6-luna', status: 'completed',
      processed_count: 2, total_count: 2, categories: [], proposals: [],
    };
    const result = autoAcceptRecognizedLayers(
      [work, openLine], { work: mapping('work'), open: mapping('open') }, recognition,
    );
    expect(result.work).toMatchObject({ kind: 'site_border', confirmed: true });
    expect(result.open).toMatchObject({ kind: 'ignore', category: null, confirmed: true });
  });

  it('does not choose between two valid work contours or replace a saved boundary', () => {
    const work = {
      ...layer('work'), source_name: 'Граница работ',
      boundary_candidate: { status: 'usable' as const, basis: 'source_surface',
        area_m2: 100, inset_1_5m_area_m2: 70, component_count: 1 },
    };
    const design = { ...work, id: 'design', source_name: 'Граница проектирования' };
    const recognition: LayerRecognition = {
      source_sha256: 'source', provider: 'openai/gpt-6-luna', status: 'completed',
      processed_count: 2, total_count: 2, categories: [], proposals: [],
    };
    const unresolved = autoAcceptRecognizedLayers(
      [work, design], { work: mapping('work'), design: mapping('design') }, recognition,
    );
    expect(unresolved.work.confirmed).toBe(false);
    expect(unresolved.design.confirmed).toBe(false);
    const chosen = { ...mapping('design'), kind: 'site_border' as const, confirmed: true };
    const preserved = autoAcceptRecognizedLayers(
      [work, design], { work: mapping('work'), design: chosen }, recognition,
    );
    expect(preserved.design).toBe(chosen);
  });

  it('keeps demolition geometry as a conservative obstacle until removal is proven', () => {
    const recognition: LayerRecognition = {
      source_sha256: 'source', provider: 'openai/gpt-6-luna', status: 'completed',
      processed_count: 1, total_count: 1,
      categories: [{ category: 'demolition_object', kind: null, label: 'Объекты демонтажа' }],
      proposals: [{ layer_id: 'demolition', category: 'demolition_object',
        confidence: 'high', evidence: [], unresolved: [] }],
    };
    const result = autoAcceptRecognizedLayers(
      [{ ...layer('demolition'), source_name: 'План|Демонтаж' }],
      { demolition: mapping('demolition') }, recognition,
    );
    expect(result.demolition).toMatchObject({
      kind: 'restricted', category: 'demolition_object', confirmed: true,
    });
  });

});
