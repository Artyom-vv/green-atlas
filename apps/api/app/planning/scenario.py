"""Временное основание предпросмотра роста, не изменяющее сохранённый план.

Сценарий передаётся генераторам и оценщику через копию контейнера проекта.
Принятые посадки содержат биологический прогноз, но не выбранный год просмотра.
"""

from app.projects.contracts import Project


def scenario_project(project: Project, year: int) -> Project:
    if project.plan is None:
        return project
    return project.model_copy(
        update={"plan": project.plan.model_copy(update={"planning_horizon_year": year})}
    )
