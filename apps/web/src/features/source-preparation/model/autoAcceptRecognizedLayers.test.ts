import { describe, expect, it } from 'vitest';
import type { Layer, LayerMapping, LayerRecognition } from '@green/api-client';
import { autoAcceptRecognizedLayers } from './autoAcceptRecognizedLayers';

type LayerRoleProposal = LayerRecognition['proposals'][number];

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
  describe('native CAD reference context', () => {
    const source = (): Layer => ({
      ...layer('reference'),
      source_name: 'Вн.ссылка_Заводский_пр',
      suggested_kind: 'utility',
      mapped_kind: 'utility',
      entity_types: { INSERT: 1 },
      context_entity_types: { INSERT: 1 },
      projected_geometry_types: {},
      unsupported_geometry_types: {},
      unreadable_geometry_count: 0,
    });
    const result = (
      proposal: Partial<LayerRoleProposal> = {},
    ): LayerRecognition => ({
      source_sha256: 'source',
      provider: 'local-rules',
      status: 'completed',
      processed_count: 1,
      total_count: 1,
      stage_processed_count: 0,
      stage_total_count: 0,
      categories: [
        {
          category: 'cad_reference',
          kind: 'ignore',
          label: 'Контекст чертежа',
        },
      ],
      proposals: [
        {
          layer_id: 'reference',
          category: 'cad_reference',
          confidence: 'high',
          evidence: [],
          unresolved: [],
          review_question: null,
          review_roles: [],
          ...proposal,
        },
      ],
    });

    it.each<Record<string, number>>([
      { INSERT: 1 },
      { 'AUTOCAD:AcDbRasterImage': 1 },
      { INSERT: 1, TEXT: 2, ATTRIB: 1 },
    ])(
      'accepts fully captured context without altering its source or visibility: %j',
      (types) => {
        const input = {
          ...source(),
          object_count: Object.values(types).reduce(
            (sum, count) => sum + count,
            0,
          ),
          entity_types: types,
          context_entity_types: types,
        };
        const before = structuredClone(input);
        const original = {
          ...mapping('reference'),
          kind: 'utility' as const,
          visible: false,
        };
        const accepted = autoAcceptRecognizedLayers(
          [input],
          { reference: original },
          result(),
        );
        expect(accepted.reference).toEqual({
          ...original,
          kind: 'ignore',
          category: 'cad_reference',
          confirmed: true,
        });
        expect(input).toEqual(before);
        expect(original.confirmed).toBe(false);
        const manual = { ...original, confirmed: true };
        expect(
          autoAcceptRecognizedLayers([input], { reference: manual }, result())
            .reference,
        ).toBe(manual);
      },
    );

    it.each<[string, Partial<Layer>, Partial<LayerRoleProposal>]>([
      ['physical projection', { projected_geometry_types: { LINE: 1 } }, {}],
      [
        'zero projection entry',
        { projected_geometry_types: { INSERT: 0 } },
        {},
      ],
      ['incomplete capture', { geometry_complete: false }, {}],
      ['unreadable geometry', { unreadable_geometry_count: 1 }, {}],
      ['unsupported object', { unsupported_geometry_types: { PROXY: 1 } }, {}],
      ['missing coverage', { unreadable_geometry_count: undefined }, {}],
      [
        'missing projection metadata',
        { projected_geometry_types: undefined },
        {},
      ],
      [
        'missing unsupported metadata',
        { unsupported_geometry_types: undefined },
        {},
      ],
      ['missing context', { context_entity_types: undefined }, {}],
      ['partial context', { object_count: 2, entity_types: { INSERT: 2 } }, {}],
      ['excess context', { context_entity_types: { INSERT: 2 } }, {}],
      [
        'extra context class',
        { context_entity_types: { INSERT: 1, TEXT: 1 } },
        {},
      ],
      [
        'unknown native class',
        {
          entity_types: { 'AUTOCAD:Custom': 1 },
          context_entity_types: { 'AUTOCAD:Custom': 1 },
        },
        {},
      ],
      [
        'physical type declared context',
        { entity_types: { LINE: 1 }, context_entity_types: { LINE: 1 } },
        {},
      ],
      [
        'empty layer',
        { object_count: 0, entity_types: {}, context_entity_types: {} },
        {},
      ],
      ['wrong object count', { object_count: 2 }, {}],
      [
        'fractional count',
        {
          object_count: 1.5,
          entity_types: { INSERT: 1.5 },
          context_entity_types: { INSERT: 1.5 },
        },
        {},
      ],
      ['medium confidence', {}, { confidence: 'medium' }],
      ['pending clarification', {}, { unresolved: ['Требуется проверка'] }],
      ['pending question', {}, { review_question: 'Уточнить роль' }],
      ['pending role choice', {}, { review_roles: ['utility'] }],
      ['conflicting calculation role', {}, { calculation_role: 'utility' }],
    ])(
      'keeps an unproven reference pending: %s',
      (_name, layerChanges, proposalChanges) => {
        // Прежняя подсказка ignore тоже не заменяет полные native-счётчики.
        for (const suggested_kind of ['utility', 'ignore'] as const) {
          const original = mapping('reference');
          const accepted = autoAcceptRecognizedLayers(
            [{ ...source(), suggested_kind, ...layerChanges }],
            { reference: original },
            result(proposalChanges),
          );
          expect(accepted.reference).toBe(original);
        }
      },
    );
  });

  it('previews only checkpointed running layers while keeping the form and pending catalog unchanged', () => {
    const layers = ['road', 'pending'].map(layer);
    const mappings = Object.fromEntries(
      layers.map((item) => [item.id, mapping(item.id)]),
    );
    const recognition: LayerRecognition = {
      source_sha256: 'source',
      provider: 'fixture',
      status: 'running',
      stage: 'reviewing_conflicts',
      processed_count: 1,
      total_count: 2,
      stage_processed_count: 0,
      stage_total_count: 1,
      completed_layer_ids: ['road'],
      categories: [
        { category: 'carriageway', kind: 'road', label: 'Проезжая часть' },
      ],
      proposals: layers.map((item) => ({
        layer_id: item.id,
        category: 'carriageway',
        confidence: 'high',
        evidence: [],
        unresolved: [],
      })),
    };
    const preview = autoAcceptRecognizedLayers(
      layers,
      mappings,
      recognition,
      true,
    );
    expect(preview.road).toMatchObject({ kind: 'road', confirmed: true });
    expect(preview.pending).toEqual(mappings.pending);
    expect(mappings.road.confirmed).toBe(false);
    expect(autoAcceptRecognizedLayers(layers, mappings, recognition)).toBe(
      mappings,
    );
    expect(
      autoAcceptRecognizedLayers(
        layers,
        mappings,
        { ...recognition, completed_layer_ids: ['road', 'road'] },
        true,
      ),
    ).toBe(mappings);
    expect(
      autoAcceptRecognizedLayers(
        layers,
        mappings,
        { ...recognition, completed_layer_ids: [] },
        true,
      ),
    ).toBe(mappings);
  });
  it('persists a high-confidence boundary category without inventing a physical role or confirmation', () => {
    const mappings = { boundary: mapping('boundary') };
    const result = autoAcceptRecognizedLayers([layer('boundary')], mappings, {
      source_sha256: 'source',
      provider: 'name-rules-v1',
      status: 'completed',
      stage_processed_count: 0,
      stage_total_count: 0,
      processed_count: 1,
      total_count: 1,
      categories: [
        { category: 'surface_boundary', kind: null, label: 'Граница покрытия' },
      ],
      proposals: [
        {
          layer_id: 'boundary',
          category: 'surface_boundary',
          confidence: 'high',
          evidence: [],
          unresolved: [],
        },
      ],
    });
    expect(result.boundary).toEqual({
      ...mappings.boundary,
      category: 'surface_boundary',
    });
    expect(result.boundary.kind).toBe('ignore');
    expect(result.boundary.confirmed).toBe(false);
    expect(mappings.boundary.category).toBeUndefined();
  });
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
      stage_processed_count: 0,
      stage_total_count: 0,
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
      ids.map((id): Layer => ({
        ...layer(id),
        entity_types: id === 'annotation' ? { TEXT: 1 } : {},
      })),
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

  it('accepts only proven non-prefix proposals after model failure and preserves exclusions and manual mappings', () => {
    const ids = ['unprocessed', 'processed', 'physical', 'manual'];
    const mappings = Object.fromEntries(ids.map((id) => [id, mapping(id)]));
    mappings.manual = { ...mappings.manual, kind: 'utility', confirmed: true };
    const recognition: LayerRecognition & { completed_layer_ids: string[] } = {
      source_sha256: 'source',
      provider: 'synthetic-recognition',
      status: 'failed',
      stage: 'classifying',
      processed_count: 3,
      total_count: 4,
      stage_processed_count: 3,
      stage_total_count: 4,
      completed_layer_ids: ['processed', 'physical', 'manual'],
      categories: [
        { category: 'building', kind: 'building', label: 'Здание' },
        { category: 'annotation', kind: 'ignore', label: 'Подписи' },
      ],
      proposals: ids.map((id) => ({
        layer_id: id,
        category: id === 'physical' ? 'annotation' : 'building',
        confidence: 'high',
        evidence: [],
        unresolved: [],
      })),
    };
    const result = autoAcceptRecognizedLayers(
      ids.map((id) => ({
        ...layer(id),
        suggested_kind: id === 'physical' ? 'utility' : 'building',
        entity_types: { LINE: 1 },
      })),
      mappings,
      recognition,
    );
    expect(result.processed).toMatchObject({
      kind: 'building',
      category: 'building',
      confirmed: true,
    });
    expect(result.unprocessed).toBe(mappings.unprocessed);
    expect(result.physical).toBe(mappings.physical);
    expect(result.manual).toBe(mappings.manual);
  });

  it.each([
    { status: 'failed', ids: undefined, processed: 1 },
    { status: 'failed', ids: [], processed: 1 },
    { status: 'failed', ids: ['building', 'building'], processed: 2 },
    { status: 'failed', ids: ['other-source'], processed: 1 },
    { status: 'failed', ids: ['building'], processed: 2 },
    { status: 'running', ids: ['building'], processed: 1 },
    { status: 'unconfigured', ids: ['building'], processed: 1 },
  ] as const)(
    'does not infer persisted answers from a count or unfinished state: $status/$ids/$processed',
    ({ status, ids, processed }) => {
      const mappings = { building: mapping('building') };
      const recognition: LayerRecognition = {
        source_sha256: 'source',
        provider: 'synthetic-recognition',
        status,
        processed_count: processed,
        total_count: 1,
        stage_processed_count: processed,
        stage_total_count: 1,
        completed_layer_ids: ids ? [...ids] : undefined,
        categories: [
          { category: 'building', kind: 'building', label: 'Здание' },
        ],
        proposals: [
          {
            layer_id: 'building',
            category: 'building',
            confidence: 'high',
            evidence: [],
            unresolved: [],
          },
        ],
      };
      expect(
        autoAcceptRecognizedLayers([layer('building')], mappings, recognition),
      ).toBe(mappings);
    },
  );

  it('does not hide a physical line even if the model calls it an annotation', () => {
    const source = {
      ...layer('questions'),
      suggested_kind: 'utility' as const,
      entity_types: { 'AUTOCAD:AcDbMLeader': 1, LWPOLYLINE: 1 },
    };
    const result = autoAcceptRecognizedLayers(
      [source],
      { questions: mapping('questions') },
      {
        source_sha256: 'source',
        provider: 'openai/gpt-6-luna',
        status: 'completed',
        stage_processed_count: 0,
        stage_total_count: 0,
        processed_count: 1,
        total_count: 1,
        categories: [
          { category: 'annotation', kind: 'ignore', label: 'Подписи' },
        ],
        proposals: [
          {
            layer_id: 'questions',
            category: 'annotation',
            confidence: 'high',
            evidence: [],
            unresolved: [],
          },
        ],
      },
    );
    expect(result.questions.confirmed).toBe(false);
  });

  it.each([
    ['native dimensions', { 'AUTOCAD:AcDbAlignedDimension': 13 }, true],
    ['attributes', { ATTRIB: 2, ATTDEF: 1 }, true],
    [
      'mixed dimensions and a line',
      { 'AUTOCAD:AcDbAlignedDimension': 13, LINE: 1 },
      false,
    ],
    [
      'unrecognised dimension class',
      { 'AUTOCAD:AcDbCustomDimension': 1 },
      false,
    ],
  ] as const)(
    'accepts only known pure annotation content: %s',
    (_name, types, accepted) => {
      const source = {
        ...layer('content'),
        suggested_kind: 'utility' as const,
        entity_types: types,
      };
      const result = autoAcceptRecognizedLayers(
        [source],
        { content: mapping('content') },
        {
          source_sha256: 'source',
          provider: 'openai/gpt-6-luna',
          status: 'completed',
          stage_processed_count: 0,
          stage_total_count: 0,
          processed_count: 1,
          total_count: 1,
          categories: [
            { category: 'annotation', kind: 'ignore', label: 'Подписи' },
          ],
          proposals: [
            {
              layer_id: 'content',
              category: 'annotation',
              confidence: 'high',
              evidence: [],
              unresolved: [],
            },
          ],
        },
      );
      expect(result.content.confirmed).toBe(accepted);
    },
  );

  it('keeps circles pending even when their layer name suggests review markers', () => {
    const source = {
      ...layer('questions'),
      source_name: 'Рецензия|Вопросы',
      entity_types: { CIRCLE: 5 },
    };
    const result = autoAcceptRecognizedLayers(
      [source],
      { questions: mapping('questions') },
      {
        source_sha256: 'source',
        provider: 'openai/gpt-6-luna',
        status: 'completed',
        stage_processed_count: 0,
        stage_total_count: 0,
        processed_count: 1,
        total_count: 1,
        categories: [
          { category: 'annotation', kind: 'ignore', label: 'Аннотация' },
        ],
        proposals: [
          {
            layer_id: 'questions',
            category: 'annotation',
            confidence: 'medium',
            evidence: [],
            unresolved: [],
          },
        ],
      },
    );
    expect(result.questions).toBeDefined();
    expect(result.questions.confirmed).toBe(false);
  });

  it.each(['high', 'medium'] as const)(
    'keeps physical lines on a named annotation layer pending (%s)',
    (confidence) => {
      const source = {
        ...layer('named'),
        source_name: 'Подписи',
        entity_types: { TEXT: 2, LINE: 1 },
      };
      const original = mapping('named');
      const result = autoAcceptRecognizedLayers(
        [source],
        { named: original },
        {
          source_sha256: 'source',
          provider: 'openai/gpt-6-luna',
          status: 'completed',
          stage_processed_count: 0,
          stage_total_count: 0,
          processed_count: 1,
          total_count: 1,
          categories: [
            { category: 'annotation', kind: 'ignore', label: 'Подписи' },
          ],
          proposals: [
            {
              layer_id: 'named',
              category: 'annotation',
              confidence,
              evidence: [],
              unresolved: [],
            },
          ],
        },
      );
      expect(result.named).toBe(original);
      expect(result.named.confirmed).toBe(false);
    },
  );

  it('uses one complete work contour ahead of a wider topographic order extent', () => {
    const work = {
      ...layer('work'),
      source_name: 'Границы работ|Граница работ',
      boundary_candidate: {
        status: 'usable' as const,
        basis: 'source_surface',
        area_m2: 76000,
        inset_1_5m_area_m2: 72000,
        component_count: 1,
      },
    };
    const order = {
      ...layer('order'),
      source_name: 'Топография|Граница заказа',
      boundary_candidate: {
        status: 'usable' as const,
        basis: 'source_surface',
        area_m2: 168000,
        inset_1_5m_area_m2: 164000,
        component_count: 1,
      },
    };
    const recognition: LayerRecognition = {
      source_sha256: 'source',
      provider: 'openai/gpt-6-luna',
      status: 'completed',
      stage_processed_count: 0,
      stage_total_count: 0,
      processed_count: 2,
      total_count: 2,
      categories: [],
      proposals: [
        {
          layer_id: 'work',
          category: 'project_boundary',
          confidence: 'high',
          evidence: [],
          unresolved: [],
        },
        {
          layer_id: 'order',
          category: 'unspecified_topography',
          confidence: 'medium',
          evidence: [],
          unresolved: [],
        },
      ],
    };
    const result = autoAcceptRecognizedLayers(
      [work, order],
      { work: mapping('work'), order: mapping('order') },
      recognition,
    );
    expect(result.work).toMatchObject({
      kind: 'site_border',
      category: 'project_boundary',
      confirmed: true,
    });
    expect(result.order).toMatchObject({
      kind: 'ignore',
      category: 'survey_reference',
      confirmed: true,
    });
  });

  it('sets aside an unusable alternative work line when one work contour is valid', () => {
    const work = {
      ...layer('work'),
      source_name: 'Границы работ|Граница работ',
      boundary_candidate: {
        status: 'usable' as const,
        basis: 'source_surface',
        area_m2: 76000,
        inset_1_5m_area_m2: 72000,
        component_count: 1,
      },
    };
    const openLine = {
      ...layer('open'),
      source_name: 'Генплан|Граница проектирования',
      boundary_candidate: {
        status: 'unavailable' as const,
        basis: 'polygonized_linework',
        area_m2: 0,
        inset_1_5m_area_m2: 0,
        component_count: 0,
        issue: 'Нет замкнутой поверхности',
      },
    };
    const recognition: LayerRecognition = {
      source_sha256: 'source',
      provider: 'openai/gpt-6-luna',
      status: 'completed',
      stage_processed_count: 0,
      stage_total_count: 0,
      processed_count: 2,
      total_count: 2,
      categories: [],
      proposals: [],
    };
    const result = autoAcceptRecognizedLayers(
      [work, openLine],
      { work: mapping('work'), open: mapping('open') },
      recognition,
    );
    expect(result.work).toMatchObject({ kind: 'site_border', confirmed: true });
    expect(result.open).toMatchObject({
      kind: 'ignore',
      category: null,
      confirmed: true,
    });
  });

  it('does not choose between two valid work contours or replace a saved boundary', () => {
    const work = {
      ...layer('work'),
      source_name: 'Граница работ',
      boundary_candidate: {
        status: 'usable' as const,
        basis: 'source_surface',
        area_m2: 100,
        inset_1_5m_area_m2: 70,
        component_count: 1,
      },
    };
    const design = {
      ...work,
      id: 'design',
      source_name: 'Граница проектирования',
    };
    const recognition: LayerRecognition = {
      source_sha256: 'source',
      provider: 'openai/gpt-6-luna',
      status: 'completed',
      stage_processed_count: 0,
      stage_total_count: 0,
      processed_count: 2,
      total_count: 2,
      categories: [],
      proposals: [],
    };
    const unresolved = autoAcceptRecognizedLayers(
      [work, design],
      { work: mapping('work'), design: mapping('design') },
      recognition,
    );
    expect(unresolved.work.confirmed).toBe(false);
    expect(unresolved.design.confirmed).toBe(false);
    const chosen = {
      ...mapping('design'),
      kind: 'site_border' as const,
      confirmed: true,
    };
    const preserved = autoAcceptRecognizedLayers(
      [work, design],
      { work: mapping('work'), design: chosen },
      recognition,
    );
    expect(preserved.design).toBe(chosen);
  });

  it('keeps demolition geometry as a conservative obstacle until removal is proven', () => {
    const recognition: LayerRecognition = {
      source_sha256: 'source',
      provider: 'openai/gpt-6-luna',
      status: 'completed',
      stage_processed_count: 0,
      stage_total_count: 0,
      processed_count: 1,
      total_count: 1,
      categories: [
        {
          category: 'demolition_object',
          kind: null,
          label: 'Объекты демонтажа',
        },
      ],
      proposals: [
        {
          layer_id: 'demolition',
          category: 'demolition_object',
          confidence: 'high',
          evidence: [],
          unresolved: [],
        },
      ],
    };
    const result = autoAcceptRecognizedLayers(
      [{ ...layer('demolition'), source_name: 'План|Демонтаж' }],
      { demolition: mapping('demolition') },
      recognition,
    );
    expect(result.demolition).toMatchObject({
      kind: 'restricted',
      category: 'demolition_object',
      confirmed: true,
    });
  });
});
