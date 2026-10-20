import { spawnSync } from 'node:child_process';
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { format, resolveConfig } from 'prettier';

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const clientRoot = join(repoRoot, 'packages/api-client');
const check = process.argv.includes('--check');
if (process.argv.slice(2).some((argument) => argument !== '--check')) {
  throw new Error('Вызов: node scripts/generate-api.mjs [--check]');
}
const python = join(
  repoRoot,
  'apps/api/.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
);
const typescriptCli = join(
  clientRoot,
  'node_modules/openapi-typescript/bin/cli.js',
);
if (!existsSync(python) || !existsSync(typescriptCli)) {
  throw new Error(
    'Установите зависимости: uv sync --project apps/api --group dev; pnpm install',
  );
}
const clientRequire = createRequire(join(clientRoot, 'package.json'));
const { generate, pascal, camel } = await import(
  pathToFileURL(clientRequire.resolve('orval')).href
);
const operationName = (operation) =>
  camel(operation.operationId.split('_api_')[0]);
const artifacts = {
  'openapi.json': join(repoRoot, 'apps/api/openapi.json'),
  'schema.d.ts': join(clientRoot, 'src/schema.d.ts'),
  'sdk.generated.ts': join(clientRoot, 'src/sdk.generated.ts'),
  'schemaTypes.generated.ts': join(clientRoot, 'src/schemaTypes.generated.ts'),
};
const partialRequests = new Set([
  'PlanChangeSetDraft',
  'RowPatternRequest',
  'FillPatternRequest',
  'RecommendationRequest',
  'AutomaticPlacementRequest',
  'BrushPreviewRequest',
  'PlanObjectCreate',
  'PlacementCheckRequest',
  'BuildingScreenRequest',
  'PlacementMaskRequest',
]);
const scratchRoot = join(repoRoot, 'apps/api/data');
mkdirSync(scratchRoot, { recursive: true });
const scratch = mkdtempSync(join(scratchRoot, 'openapi-generation-'));
const target = (name) => (check ? join(scratch, name) : artifacts[name]);

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: repoRoot,
    stdio: 'inherit',
    windowsHide: true,
    env: {
      ...process.env,
      GREEN_ATLAS_DB_PATH: join(scratch, 'schema.sqlite3'),
    },
  });
  if (result.error) throw result.error;
  if (result.status !== 0)
    throw new Error(`Команда ${command} завершилась с кодом ${result.status}`);
}

async function generateAliases(spec, operations) {
  // Общий контракт сохраняет прежние типы ответов и разрешённые входные значения по умолчанию.
  const aliases = [
    '// Сгенерировано из OpenAPI. Общие типы принадлежат контракту API.',
    "import type { WireSchema } from './contracts/wire';",
    "import type { paths } from './schema';",
    ...Object.keys(spec.components.schemas).map((name) =>
      partialRequests.has(name)
        ? `export type { ${name} } from './contracts/planning';`
        : `export type ${pascal(name)} = WireSchema<'${name}'>;`,
    ),
    ...operations
      .filter(({ operation }) =>
        operation.parameters?.some((p) => p.in === 'query'),
      )
      .map(
        ({ path, method, operation }) =>
          `export type ${pascal(operationName(operation))}Params = NonNullable<paths['${path}']['${method}']['parameters']['query']>;`,
      ),
    ...operations.flatMap(({ path, method, operation }) =>
      Object.entries(operation.responses)
        .filter(([, response]) => {
          const schema = response.content?.['application/json']?.schema;
          return schema && !schema.$ref;
        })
        .map(
          ([status]) =>
            `export type ${pascal(operationName(operation))}${status} = paths['${path}']['${method}']['responses'][${status}]['content']['application/json'];`,
        ),
    ),
    '',
  ].join('\n');
  const path = artifacts['schemaTypes.generated.ts'];
  writeFileSync(
    target('schemaTypes.generated.ts'),
    await format(aliases, { ...(await resolveConfig(path)), filepath: path }),
  );
}

async function generateSdk(spec, operations) {
  // Одинаковая структура каталогов сохраняет относительный импорт транспорта
  // при проверке генерации, не перезаписывая рабочий SDK.
  const mutatorPath = check
    ? join(scratch, 'transport/request.ts')
    : join(clientRoot, 'src/transport/request.ts');
  if (check)
    cpSync(join(clientRoot, 'src/transport'), dirname(mutatorPath), {
      recursive: true,
    });
  // SDK содержит исполняемый контракт; описания остаются в OpenAPI.
  for (const { operation } of operations) {
    delete operation.summary;
    delete operation.description;
  }
  await generate(
    {
      input: { target: spec },
      output: {
        target: target('sdk.generated.ts'),
        client: 'fetch',
        mode: 'single',
        schemas: {
          path: join(scratch, 'orval-models.ts'),
          mode: 'single',
          importPath: '@green/api-client/schema-types',
        },
        urlEncodeParameters: true,
        override: {
          header: () => [
            'Сгенерировано Orval из OpenAPI. Изменяйте контракт, а не этот файл.',
          ],
          operationName,
          mutator: { path: mutatorPath, name: 'request' },
          fetch: {
            includeHttpResponseReturnType: false,
            forceSuccessResponse: true,
            arrayFormat: 'repeat',
          },
        },
      },
    },
    repoRoot,
    { throwOnError: true },
  );
  const path = artifacts['sdk.generated.ts'];
  writeFileSync(
    target('sdk.generated.ts'),
    await format(readFileSync(target('sdk.generated.ts'), 'utf8'), {
      ...(await resolveConfig(path)),
      filepath: path,
    }),
  );
}

try {
  run(python, [
    join(repoRoot, 'apps/api/scripts/export_openapi.py'),
    '--output',
    target('openapi.json'),
  ]);
  run(process.execPath, [
    typescriptCli,
    target('openapi.json'),
    '-o',
    target('schema.d.ts'),
  ]);
  const spec = JSON.parse(readFileSync(target('openapi.json'), 'utf8'));
  const operations = Object.entries(spec.paths).flatMap(([path, methods]) =>
    Object.entries(methods)
      .filter(([, operation]) => operation.operationId)
      .map(([method, operation]) => ({ path, method, operation })),
  );
  await generateAliases(spec, operations);
  await generateSdk(spec, operations);
  if (check) {
    const normalized = (path) =>
      existsSync(path)
        ? readFileSync(path, 'utf8').replaceAll('\r\n', '\n')
        : null;
    const changed = Object.entries(artifacts)
      .filter(([name, path]) => normalized(path) !== normalized(target(name)))
      .map(([, path]) => path);
    if (changed.length) {
      console.error(
        `Контракт API устарел. Выполните pnpm generate:api:\n${changed.join('\n')}`,
      );
      process.exitCode = 1;
    } else console.log('Контракт и SDK соответствуют текущему API.');
  }
} finally {
  // Удаляется только каталог, созданный этим запуском генерации.
  if (dirname(resolve(scratch)) !== resolve(scratchRoot))
    throw new Error('Недопустимый временный каталог');
  rmSync(scratch, { recursive: true, force: true });
}
