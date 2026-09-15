import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const check = process.argv.includes('--check');
if (process.argv.slice(2).some((argument) => argument !== '--check')) {
  throw new Error('Usage: node scripts/generate-api.mjs [--check]');
}
const python = join(repoRoot, 'apps/api/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const typescriptCli = join(repoRoot, 'packages/api-client/node_modules/openapi-typescript/bin/cli.js');
if (!existsSync(python) || !existsSync(typescriptCli)) {
  throw new Error('Install dependencies first: uv sync --project apps/api --group dev; pnpm install');
}
const scratchRoot = join(repoRoot, 'apps/api/data');
mkdirSync(scratchRoot, { recursive: true });
const scratch = mkdtempSync(join(scratchRoot, 'openapi-generation-'));
const schemaPath = join(repoRoot, 'apps/api/openapi.json');
const typesPath = join(repoRoot, 'packages/api-client/src/schema.d.ts');

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: repoRoot,
    stdio: 'inherit',
    windowsHide: true,
    env: { ...process.env, GREEN_ATLAS_DB_PATH: join(scratch, 'schema.sqlite3') },
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} failed with status ${result.status}`);
}

function normalized(path) {
  return existsSync(path) ? readFileSync(path, 'utf8').replaceAll('\r\n', '\n') : null;
}

try {
  const schemaOutput = check ? join(scratch, 'openapi.json') : schemaPath;
  const typesOutput = check ? join(scratch, 'schema.d.ts') : typesPath;
  run(python, [join(repoRoot, 'apps/api/scripts/export_openapi.py'), '--output', schemaOutput]);
  run(process.execPath, [typescriptCli, schemaOutput, '-o', typesOutput]);
  if (check) {
    const changed = [[schemaPath, schemaOutput], [typesPath, typesOutput]]
      .filter(([current, generated]) => normalized(current) !== normalized(generated))
      .map(([current]) => current);
    if (changed.length > 0) {
      console.error(`API contract is stale. Run pnpm generate:api:\n${changed.join('\n')}`);
      process.exitCode = 1;
    } else {
      console.log('API contract matches the current backend.');
    }
  }
} finally {
  // Only the directory created by mkdtemp above can be removed.
  if (dirname(resolve(scratch)) !== resolve(scratchRoot)) throw new Error('Invalid scratch directory');
  rmSync(scratch, { recursive: true, force: true });
}
