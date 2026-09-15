import type { components } from '../schema';
export type Schemas = components['schemas'];
export type WireSchema<Name extends keyof Schemas> = Schemas[Name];
