export const MAP_DESIGN_PALETTE: Readonly<
  Record<string, readonly [string, string]>
> = {
  building: ['#ccd3df', '#64748b'],
  road: ['#f0e8da', '#aa9475'],
  existing_green: ['#abd4c1', '#287652'],
  lawn: ['#d0e7ac', '#709241'],
  water: ['#c0dff2', '#4287b1'],
  planting_area: ['rgba(0,0,0,0)', '#84948c'],
  site_border: ['rgba(0,0,0,0)', '#35414e'],
  allowed: ['rgba(25,135,84,.025)', 'rgba(25,135,84,.13)'],
  utility: ['rgba(0,0,0,0)', '#8a4b00'],
  restricted: ['rgba(183,100,0,.05)', '#8a4b00'],
};

export const MAP_DESIGN_CONTEXT_STROKE = '#85919e';
export const MAP_DESIGN_TEXT_COLOR = '#596675';

/** Legend and both renderers share the same semantic colours. */
export const MAP_DESIGN_LEGEND = [
  { kind: 'building', label: 'Здания', shape: 'area' },
  { kind: 'road', label: 'Дороги и покрытия', shape: 'area' },
  { kind: 'lawn', label: 'Газоны', shape: 'area' },
  {
    kind: 'existing_green',
    label: 'Существующая растительность',
    shape: 'area',
  },
  { kind: 'water', label: 'Вода', shape: 'area' },
  { kind: 'utility', label: 'Инженерные сети', shape: 'line' },
  { kind: 'restricted', label: 'Технические объекты', shape: 'line' },
] as const;
