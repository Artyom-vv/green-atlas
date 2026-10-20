import { expect, it } from 'vitest';
import { locationSuggestion } from './locationFixtures';
import { locationDescription, locationLabel } from './locationPresentation';
it('keeps street type in the label without repeating it as an attribute', () => {
  const item = {
    ...locationSuggestion,
    street: 'Задонский',
    label: '115580, г Москва, р-н Зябликово, Задонский пр-д',
  };
  expect(locationLabel(item)).toBe('Москва, Задонский проезд');
  expect(locationDescription(item)).toBe('р-н Зябликово');
});
it('keeps regional disambiguation for streets with the same name', () => {
  const item = {
    ...locationSuggestion,
    city: 'Лесной',
    street: 'Мира',
    label: 'Свердловская обл, г Лесной, ул Мира',
  };
  expect(locationLabel(item)).toBe('Лесной, Мира');
  expect(locationDescription(item)).toBe('Свердловская обл');
});
