import { defineConfig } from 'vite';
import { createReadStream } from 'node:fs';
import { stat, mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const repository = resolve(import.meta.dirname, '../..');
const fixtures = {
  '/fixture/apot.dxf': resolve(repository, '.runtime/cad-cache/d091470f4c3fdac077ebee44d4b1a63d97171e08a3872dcd49f355b570f53640/e5670f98539f4d8b45088231056bbffd8e43aaa458065b6fd22bc3cf7916eae7.dxf'),
  '/fixture/native.dxf': 'D:/Downloads/лцт/Датасет/Датасет/Пилотный проект 20 улиц/Пилотный проект 20 улиц/3. 3-я Парковая/Исходные данные/10001759_3-я Парковая ул/00_Ссылки/АСУ ОДХ/117861691400001.dxf',
  '/assets/mtext-renderer-worker.js': resolve(import.meta.dirname, 'node_modules/@mlightcad/cad-simple-viewer/dist/mtext-renderer-worker.js'),
};
export default defineConfig({
  server: { host: '127.0.0.1', port: 5194, strictPort: true, hmr: false,
    watch: { ignored: ['**/public/fonts/**', '**/*.log', '**/*.pid'] } },
  plugins: [{ name: 'official-cad-fixtures', configureServer(server) {
    server.middlewares.use(async (request, response, next) => {
      const file = fixtures[request.url?.split('?')[0]];
      if (file) {
        try {
          const info = await stat(file);
          response.setHeader('Content-Length', info.size);
          response.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : 'application/octet-stream');
          createReadStream(file).pipe(response);
        } catch (error) { response.statusCode = 500; response.end(String(error)); }
        return;
      }
      if (request.url === '/evidence' && request.method === 'POST') {
        let body = '';
        for await (const chunk of request) {
          body += chunk;
          if (body.length > 1_000_000) { response.statusCode = 413; response.end(); return; }
        }
        const result = JSON.parse(body);
        const directory = resolve(repository, 'docs/implementation/2026-09-15-official-implementation/map/sdk-evidence');
        await mkdir(directory, { recursive: true });
        await writeFile(resolve(directory, `${Date.now()}.json`), JSON.stringify(result, null, 2));
        response.end('saved');
        return;
      }
      next();
    });
  } }],
});
