import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath } from 'node:url';

const srcPath = fileURLToPath(new URL('./src', import.meta.url));

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': srcPath,
    },
  },
  server: {
    port: 5173,
    // The reproducible asset pipeline keeps large source packages and the
    // pinned KTX toolchain here. Rebuilding assets must not trigger thousands
    // of dev-page reloads while the cache is populated.
    watch: { ignored: ['**/.cache/plant-assets/**'] },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
    exclude: ['e2e/**', 'node_modules/**'],
  },
});
