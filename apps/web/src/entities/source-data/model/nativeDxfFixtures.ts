import type { NativeDxfSourceAsset, Project } from '@green/api-client';

export const nativeProject: Project = {
  id: 'native',
  name: 'Uploaded DXF',
  status: 'imported',
  state_version: 4,
  geometry_version: 2,
  map_ready: true,
  import_status: { mode: 'source_dxf', editability: 'editable', message: '' },
  source_file: {
    accept_partial_geometry: false,
    name: 'source.dxf',
    size: 1000,
    imported_at: '2026-09-15',
    dxf_version: 'AC1027',
    units: 'м',
    units_assumed: false,
    content_sha256: 'a'.repeat(64),
    entity_count: 2,
  },
};

export const nativeAsset: NativeDxfSourceAsset = {
  project_id: 'native',
  source_sha256: 'a'.repeat(64),
  source_bytes: 1000,
  source_name: 'source.dxf',
  unit_scale_to_m: 1,
  units_assumed: false,
  scale_basis: 'imported_units',
  format: 'dxf_ascii',
  file_encoding: 'utf-8',
  scope: 'uploaded_drawing',
  bounds_m: [-10, -10, 10, 10],
  file_url: `/api/projects/native/source-dxf/download?expected_source_sha256=${'a'.repeat(64)}`,
};
