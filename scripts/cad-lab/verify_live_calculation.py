"""Read a live API project and exercise calculation in an isolated QA database.

Never confirms mappings, plants objects, or modifies the user's project.
The QA confirmation tests the computation pipeline, NOT semantic correctness.
"""
import argparse
import json
import os
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--qa-db', required=True)
    args = parser.parse_args()
    os.environ['GREEN_ATLAS_DB_PATH'] = args.qa_db
    from app.composition import get_application, close_runtime
    from app.projects.contracts import Project
    from app.dxf_import.layer_contracts import LayerMapping

    response = httpx.get(args.url, params={'include_geometry': 'true'}, timeout=120)
    response.raise_for_status()
    project = Project.model_validate(response.json())
    original_id, original_version = project.id, project.state_version
    application = get_application()
    project.id = str(uuid4())
    project.name = 'QA isolated native calculation'
    # Fixture publication only: all production actions below use the facade.
    application.repository.create(project)
    application.accept_partial_geometry(project.id, project.source_file.content_sha256)
    boundaries = [layer for layer in project.layers
                  if layer.boundary_candidate and layer.boundary_candidate.status == 'usable']
    boundary = max(boundaries, key=lambda layer: layer.boundary_candidate.area_m2)
    application.save_mappings(project.id, [LayerMapping(
        layer_id=layer.id,
        kind='site_border' if layer.id == boundary.id else layer.mapped_kind or 'ignore',
        confirmed=True, visible=layer.visible,
    ) for layer in project.layers])
    op = application.start_geometry_operation(project.id)
    application.run_geometry_operation(op.id)
    op = application.get_operation(project.id, op.id)
    calculated = application.get(project.id)
    result = {
        'source_project': original_id, 'source_state_version': original_version,
        'qa_project': project.id, 'qa_database': args.qa_db,
        'mapping_basis': 'automatic proposals confirmed ONLY in isolated QA; not semantic acceptance',
        'boundary': boundary.source_name,
        'operation': op.model_dump(mode='json'),
        'scope': calculated.geometry.calculation_scope if calculated.geometry else None,
        'site_area_m2': calculated.site_area_m2,
        'allowed_area_m2': calculated.allowed_area_m2,
        'features_after': len(calculated.geometry.feature_collection['features']) if calculated.geometry else 0,
    }
    final = httpx.get(args.url, params={'include_geometry': 'false'}, timeout=120).json()
    result['user_project_unchanged'] = final['state_version'] == original_version
    print(json.dumps(result, ensure_ascii=False, indent=2))
    close_runtime()
    if op.status != 'completed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
