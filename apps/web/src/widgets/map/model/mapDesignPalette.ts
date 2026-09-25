export const MAP_DESIGN_PALETTE: Readonly<
  Record<string, readonly [string, string]>
> = {
  building: ['#e0e4e8', '#85919e'],
  road: ['#fafbfc', '#c7ced6'],
  existing_green: ['#d1eadc', '#7daa90'],
  lawn: ['#e5efdb', '#839b70'],
  water: ['#cad8ff', '#9db6ff'],
  planting_area: ['rgba(0,0,0,0)', '#84948c'],
  site_border: ['rgba(0,0,0,0)', '#35414e'],
  allowed: ['rgba(25,135,84,.025)', 'rgba(25,135,84,.13)'],
  utility: ['rgba(0,0,0,0)', '#8a4b00'],
  restricted: ['rgba(183,100,0,.05)', '#8a4b00'],
};

export const MAP_DESIGN_CONTEXT_STROKE = '#85919e';
export const MAP_DESIGN_TEXT_COLOR = '#596675';
