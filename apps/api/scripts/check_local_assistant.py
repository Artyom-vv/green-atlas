"""Small live-model acceptance set. No projects, DXF or database access."""
import json
import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.planning_assistant import interpret_task


CASES = [
    ('Больше тени, не более 40 посадок', 'shade', 40, False),
    ('Нужна связность озеленения, максимум 25 посадок', 'continuity', 25, False),
    ('Уменьшить конфликты при росте на горизонте 40 лет, максимум 12 посадок', 'low_future_conflict', 12, False),
    ('На участке 7 нужна тень через 30 лет', 'shade', None, False),
    ('Посади ровно 50 лип в два ряда', None, None, True),
    ('Удали существующие деревья и поставь 20 новых', None, None, True),
    ('Баланс между разными целями. Не больше 18 посадок.', 'balanced', 18, False),
    ('На 6 выбранных участках нужна связность. Всего до 32 растений.', 'continuity', 32, False),
    ('Больше тени. Всего максимум 15 растений. Соблюдай ограничения проекта.', 'shade', 15, False),
    ('Максимум 30 растений, только на влажной почве', None, None, True),
]

if __name__ == '__main__':
    failed = 0
    for task, profile, quantity, unsupported in CASES:
        started = perf_counter()
        result = interpret_task(task, 'qwen3.5:4b')
        passed = bool(result.unsupported) if unsupported else result.arrangement == 'area' and result.profile == profile and result.max_sites == quantity and not result.unsupported
        failed += not passed
        print(json.dumps({'task': task, 'seconds': round(perf_counter() - started, 2), 'passed': passed, 'result': result.model_dump()}, ensure_ascii=False), flush=True)
    for task in [
        'Нужно по контурам зданий посадить группы деревьев, чтобы было красиво по местности и здания не были видны слишком',
        'Прикрой жилые дома деревьями',
        'Хочу группы вдоль фасадов, чтобы двор выглядел зеленее',
    ]:
        started = perf_counter()
        result = interpret_task(task, 'qwen3.5:4b')
        passed = result.arrangement == 'building_screen' and not result.unsupported and result.max_sites is None and not result.questions
        failed += not passed
        print(json.dumps({'task': task, 'seconds': round(perf_counter() - started, 2), 'passed': passed, 'result': result.model_dump()}, ensure_ascii=False), flush=True)
    raise SystemExit(1 if failed else 0)
