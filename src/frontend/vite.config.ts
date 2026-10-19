import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// package.json version is kept in sync with the repo VERSION file (scripts/check_versions.py).
const here = dirname(fileURLToPath(import.meta.url));
const bundleVersion = JSON.parse(readFileSync(resolve(here, 'package.json'), 'utf8')).version as string;

// In dev (`npm run dev`) /api is proxied to the API container on 127.0.0.1:8381, which is published
// only by docker-compose.dev.yml (docker compose -f docker-compose.yml -f docker-compose.dev.yml up).
export default defineConfig({
  plugins: [react()],
  define: {
    __HEARDLOOP_BUNDLE_VERSION__: JSON.stringify(bundleVersion),
  },
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8381' },
  },
});
