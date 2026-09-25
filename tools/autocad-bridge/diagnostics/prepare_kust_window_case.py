"""Explicit RESEARCH roles for the recorded Kustanayskaya window, not a classifier.

Every observed layer is emitted. Unassigned layers remain unknown, annotations
are separately counted by native type, and all nonblocked points stay DRAFT.
No product mapping, policy, CAD file or user project is changed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_direct_queries import digest


def role(layer: str) -> tuple[str, float]:
    topo = '00.1_10004141_Топография|'
    project = '03_10004141_Проектные решения|'
    if layer.startswith(topo):
        name = layer[len(topo):]
        if name in {'Здания', 'Части зданий', 'Крыльца', 'Лестницы набережных', 'Навесы'}:
            return 'area', 1.5
        if name in {'Бортовой камень', 'Граница улицы', 'Граница площадки', 'Ограды',
                    'ЛЭП', 'Светофоры', 'Столбы', 'Фонари', 'Колодцы', 'Вентиляторы',
                    'Фонтаны', 'Отдельно стоящее дерево', 'Полоса деревьев'}:
            return 'curve', 1.0
        if name in {'Горизонтали', 'Координатные кресты', 'Рамки', 'Номер дома', 'Внутреннее заполнение'}:
            return 'context', 0
    if layer.startswith('00.2_10004141_Сети|'):
        return 'curve', 1.0  # Every named network, not a sample of cable segments.
    if layer.startswith(project):
        name = layer[len(project):]
        if name in {'ДВ_ПП_Тип0_Спец. Покрытие', 'ДВ_ПП_Тип0_Спец. Покрытие за газон',
                    'ДВ_ПП_Тип2_ПЧ местные Ремонт', 'ДВ_ПП_Тип4_ПЧ местные за Газон',
                    'ДВ_ПП_Тип4_ПЧ местные за ПЧ местные', 'ДВ_ПП_Тип4_ПЧ местные за Тротуар АБ',
                    'ДВ_ПП_Тип5_Тротуар АБ Ремонт', 'ДВ_ПП_Тип6_Тротуар АБ более 2м за Газон',
                    'ДВ_ПП_Тип6_Тротуар АБ более 2м за ПЧ', 'ДВ_ПП_Тип7_Тротуар АБ менее 2м за Газон',
                    'ДВ_ПП_Тип7_Тротуар АБ менее 2м за ПЧ', 'ДВ_ГП_П_Воздуховоды'}:
            return 'area', 0.5
        if name in {'ДВ_Борт_БР100.30.15', 'ДВ_Борт_БР100.45.18', 'ДВ_ГП_П_Борт_Пониженный', 'Подпорная стенка'}:
            return 'curve', 0.5
        if name == 'ДВ_ГП_П_ОФР_Размеры_осн':
            return 'context', 0
    if layer in {'04_10004141_ИОТ1|ИОТ1_КЛ НО сущ.', '04_10004141_ИОТ1|ИОТ1_замена светильника',
                 '04_10004141_ИОТ1|ЭН_СУЩ.ПОД. ПРОВОД', 'ИОТ2|ЭС_КЛ_0.2-0.4_кВ - 4', 'ИОТ2|ЭС_Футляры'}:
        return 'curve', 1.0
    # Law/boundary symbols, uncertain landscaping/demolition and bare layer0
    # must not silently become ignored or imaginary occupied polygons.
    return 'unknown', 0


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--window-case', type=Path, required=True)
    parser.add_argument('--controls', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output must be new')
    report = json.loads(args.inventory.read_text())
    window = report['inventory']['window_inventory']
    case = json.loads(args.window_case.read_text())
    if window['window'] != case['window'] or window['padding'] != case['padding']:
        parser.error('Native inventory window differs from requested case')
    layers = sorted({row['effective_layer'] for row in window['near']})
    controls = json.loads(args.controls.read_text())['controls']
    blocked = [c['point'] for c in controls if c['expected'] == 'blocked']
    case['calculation'] = {
        'scope': 'Explicit experimental roles only. Offsets are drawing-unit test values, NOT norms. All outcomes are draft or known blocked; unknown roles/area failures remain visible. Classifier and production state are unchanged.',
        'native_inventory_sha256': digest(args.inventory),
        'site_route': '6E16/16020', 'grid_step': 2, 'spacing': 2.15,
        'layer_rules': [{'layer': layer, 'role': role(layer)[0], 'clearance': role(layer)[1]} for layer in layers],
        'blocked_controls': blocked,
        'control_provenance': str(args.controls),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(case, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'layers': len(layers), 'blocked_controls': len(blocked), 'output': str(args.output)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
