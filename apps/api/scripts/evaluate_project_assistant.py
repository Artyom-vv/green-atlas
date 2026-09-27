"""Manual local-model smoke checks; no plan mutations or external requests."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.project_assistant import ProjectChatInput, interpret_chat

cases = [
    ('Посади группы деревьев вдоль зданий на всех участках, не больше 10 посадок', 'building_screen', 'project', 10),
    ('Нужно по контурам зданий посадить группы деревьев, чтобы было красиво по местности и здания не были видны слишком', 'building_screen', 'ask', None),
    ('Удали всё озеленение с участка', 'delete', 'ask', None),
    ('Больше тени на всех участках, максимум 50 новых посадок', 'recommend', 'project', 50),
    ('Не удаляй деревья. Сколько всего посадок в проекте?', 'reply', None, None),
    ('Посади только липы вдоль зданий на всех участках', 'reply', None, None),
]
failures = 0
for message, action, scope, count in cases:
    request = ProjectChatInput(message=message, project_name='Зарядье', planting_count=239, selected_count=0,
        zones=[{'id': 'west', 'label': 'Западный', 'planting_count': 22}],
        history=[{'role': 'assistant', 'content': 'Для всего проекта лимит устанавливается отдельно.'}])
    result = interpret_chat(request, 'qwen3.5:4b')
    passed = result.action == action and (scope is None or result.scope == scope) and (count is None or result.max_sites == count)
    failures += not passed
    print(json.dumps({'passed': passed, 'message': message, 'result': result.model_dump()}, ensure_ascii=False), flush=True)
raise SystemExit(1 if failures else 0)
