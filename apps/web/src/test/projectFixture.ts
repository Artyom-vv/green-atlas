import type { Project } from '@green/api-client';

export function projectFixture(): Project {
  return {
    id: 'focus-project',
    name: 'Восточный сквер',
    status: 'editing',
    state_version: 3,
    geometry_version: 2,
    map_ready: true,
    plan: null,
    planting_zones: [
      {
        id: 'zone-east',
        label: 'Восточный сквер',
        geometry: {
          type: 'Polygon',
          coordinates: [
            [
              [1.25, 2],
              [31.25, 2],
              [31.25, 42],
              [1.25, 42],
              [1.25, 2],
            ],
            [
              [5, 5],
              [5, 10],
              [10, 10],
              [10, 5],
              [5, 5],
            ],
          ],
        },
      },
    ],
  };
}
