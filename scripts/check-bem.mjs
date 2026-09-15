import { readFile, readdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, extname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const requireWeb = createRequire(join(root, 'apps/web/package.json'));
const ts = requireWeb('typescript');
const bemName = /^[A-Za-z][\w-]*(?:__|--)[\w-]+$/;
const utilities = new Set([
  'cx',
  'cn',
  'clsx',
  'classNames',
  'cva',
  'tv',
  'twMerge',
]);

function classesInSelector(selector) {
  return [...selector.matchAll(/\.([A-Za-z][\w-]*)/g)]
    .map((match) => match[1])
    .filter((name) => bemName.test(name));
}

/** Check rule selectors, never declarations, CSS custom properties or comments. */
export function findBemInCss(source) {
  const clean = source.replace(/\/\*[\s\S]*?\*\//g, (comment) =>
    comment.replace(/[^\n]/g, ' '),
  );
  const found = [];
  let start = 0;
  let quote;
  for (let index = 0; index < clean.length; index += 1) {
    const character = clean[index];
    if (quote) {
      if (character === '\\') index += 1;
      else if (character === quote) quote = undefined;
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      continue;
    }
    if (character === '{') {
      const selector = clean.slice(start, index);
      if (!selector.trimStart().startsWith('--')) {
        for (const name of classesInSelector(selector)) {
          const position = start + selector.indexOf(name);
          found.push({
            name,
            line: clean.slice(0, position).split('\n').length,
          });
        }
      }
      start = index + 1;
    } else if (character === ';' || character === '}') start = index + 1;
  }
  return found;
}

/** Read only class-bearing expressions. A string in a label, route, data field,
 * or CSS variable cannot become a BEM violation merely because it contains --. */
export function findBemInScript(source, filename = 'source.tsx') {
  const file = ts.createSourceFile(
    filename,
    source,
    ts.ScriptTarget.Latest,
    true,
    /x$/.test(extname(filename)) ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const bindings = new Map();
  const output = new Map();
  function bind(node) {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.initializer
    )
      bindings.set(node.name.text, node.initializer);
    ts.forEachChild(node, bind);
  }
  bind(file);
  function record(node, text) {
    const names = new Set([
      ...text.split(/\s+/).filter((name) => bemName.test(name)),
      ...classesInSelector(text),
    ]);
    for (const name of names) {
      const line =
        file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1;
      output.set(`${line}:${name}`, { name, line });
    }
  }
  function collect(node, seen = new Set(), dictionaryKeys = false) {
    if (!node || seen.has(node)) return;
    const visited = new Set(seen).add(node);
    if (ts.isStringLiteralLike(node)) {
      record(node, node.text);
      return;
    }
    if (ts.isTemplateExpression(node)) {
      record(node.head, node.head.text);
      for (const span of node.templateSpans) {
        collect(span.expression, visited);
        record(span.literal, span.literal.text);
      }
      return;
    }
    if (ts.isIdentifier(node)) {
      collect(bindings.get(node.text), visited);
      return;
    }
    if (ts.isConditionalExpression(node)) {
      collect(node.whenTrue, visited);
      collect(node.whenFalse, visited);
      return;
    }
    if (ts.isBinaryExpression(node)) {
      if (
        [
          ts.SyntaxKind.AmpersandAmpersandToken,
          ts.SyntaxKind.BarBarToken,
          ts.SyntaxKind.QuestionQuestionToken,
          ts.SyntaxKind.PlusToken,
        ].includes(node.operatorToken.kind)
      ) {
        collect(node.left, visited);
        collect(node.right, visited);
      }
      return;
    }
    if (ts.isPropertyAssignment(node)) {
      if (dictionaryKeys && ts.isStringLiteralLike(node.name))
        record(node.name, node.name.text);
      collect(node.initializer, visited);
      return;
    }
    if (ts.isCallExpression(node)) {
      const name = ts.isIdentifier(node.expression)
        ? node.expression.text
        : undefined;
      const keys = ['cx', 'cn', 'clsx', 'classNames'].includes(name);
      if (ts.isPropertyAccessExpression(node.expression))
        collect(node.expression.expression, visited);
      for (const argument of node.arguments) collect(argument, visited, keys);
      return;
    }
    ts.forEachChild(node, (child) => collect(child, visited, dictionaryKeys));
  }
  function visit(node) {
    if (ts.isJsxAttribute(node) && node.name.getText(file) === 'className')
      collect(node.initializer);
    if (
      ts.isBinaryExpression(node) &&
      node.operatorToken.kind === ts.SyntaxKind.EqualsToken &&
      ts.isPropertyAccessExpression(node.left) &&
      node.left.name.text === 'className'
    )
      collect(node.right);
    if (ts.isCallExpression(node)) {
      if (
        ts.isIdentifier(node.expression) &&
        utilities.has(node.expression.text)
      )
        collect(node);
      if (ts.isPropertyAccessExpression(node.expression)) {
        const target = node.expression.expression;
        if (
          ts.isPropertyAccessExpression(target) &&
          target.name.text === 'classList' &&
          ['add', 'remove', 'toggle', 'replace'].includes(
            node.expression.name.text,
          )
        ) {
          for (const argument of node.arguments) collect(argument);
        }
        if (
          node.expression.name.text === 'setAttribute' &&
          node.arguments[0] &&
          ts.isStringLiteralLike(node.arguments[0]) &&
          node.arguments[0].text === 'class'
        )
          collect(node.arguments[1]);
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(file);
  return [...output.values()];
}

async function sourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(
    entries
      .filter(
        (entry) => !['node_modules', 'dist', 'generated'].includes(entry.name),
      )
      .map(async (entry) => {
        const path = join(directory, entry.name);
        return entry.isDirectory()
          ? sourceFiles(path)
          : /\.(?:[cm]?[jt]sx?|css)$/.test(entry.name) &&
              !/\.test\./.test(entry.name)
            ? [path]
            : [];
      }),
  );
  return nested.flat();
}

export async function checkBem(directories) {
  const files = (await Promise.all(directories.map(sourceFiles))).flat();
  const findings = [];
  for (const file of files.sort()) {
    const source = await readFile(file, 'utf8');
    const items =
      extname(file) === '.css'
        ? findBemInCss(source)
        : findBemInScript(source, file);
    for (const finding of items)
      findings.push({
        file: relative(root, file).replaceAll('\\', '/'),
        ...finding,
      });
  }
  return findings;
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const directories = process.argv.slice(2);
  const findings = await checkBem(
    (directories.length
      ? directories
      : ['apps/web/src', 'packages/ui/src']
    ).map((path) => resolve(root, path)),
  );
  for (const finding of findings)
    console.error(`${finding.file}:${finding.line} BEM class: ${finding.name}`);
  if (findings.length) process.exitCode = 1;
  else
    console.log('BEM class check passed. CSS custom properties are excluded.');
}
