import { test } from 'node:test';
import assert from 'node:assert/strict';
import { findBemInCss, findBemInScript } from './check-bem.mjs';

test('checks CSS selectors without mistaking custom properties or comments for class names', () => {
  const result = findBemInCss(
    '/* .comment__only {} */\n.panel__body, .panel--open { --space-token: 4px; --not__a-class: "a--b"; color: var(--color-panel); }',
  );
  assert.deepEqual(
    result.map((item) => item.name),
    ['panel__body', 'panel--open'],
  );
});
test('ignores BEM-shaped data, CSS variables and ordinary Tailwind classes', () => {
  assert.deepEqual(
    findBemInScript(
      'const data = "report__value"; const route = "a--b"; <div className="[--gap:8px] bg-[var(--surface)] min-h-0" style={{ "--local__token": "4px" }} aria-label="label__text" />;',
    ),
    [],
  );
});
test('follows class attributes and local class constants, conditional branches and utility arguments', () => {
  const result = findBemInScript(
    'const classes = "panel__body"; const styles = cx(classes, mode === "data__value" && "panel--open"); <div className={styles} />;',
  );
  assert.deepEqual(result.map((item) => item.name).sort(), [
    'panel--open',
    'panel__body',
  ]);
});
test('checks arbitrary selector variants and native classList assignments', () => {
  const result = findBemInScript(
    '<div className="[&_.panel__row]:gap-2" />; element.classList.add("map--active"); element.className = "map__body";',
  );
  assert.deepEqual(result.map((item) => item.name).sort(), [
    'map--active',
    'map__body',
    'panel__row',
  ]);
});
test('checks class utility dictionary keys but does not inspect variant keys as class values', () => {
  const result = findBemInScript(
    'cx({ "panel--active": active }); tv({ variants: { "domain__state": { on: "text-sm" } }, base: "map__body" });',
  );
  assert.deepEqual(result.map((item) => item.name).sort(), [
    'map__body',
    'panel--active',
  ]);
});
