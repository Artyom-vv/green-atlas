export const MAP_DESIGN_PALETTE: Readonly<
  Record<string, readonly [string, string]>
> = {
  building: ['#8a98aa', '#4b586b'],
  road: ['#f0e8da', '#aa9475'],
  existing_green: ['#2d7a57', '#19543b'],
  lawn: ['#d0e7ac', '#709241'],
  water: ['#429eb8', '#246780'],
  planting_area: ['rgba(0,0,0,0)', '#84948c'],
  site_border: ['rgba(0,0,0,0)', '#35414e'],
  allowed: ['rgba(25,135,84,.025)', 'rgba(25,135,84,.13)'],
  utility: ['rgba(0,0,0,0)', '#536b8a'],
  restricted: ['rgba(183,100,0,.05)', '#925e30'],
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
