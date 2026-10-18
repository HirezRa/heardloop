import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In dev (`npm run dev`) /api is proxied to the API container on 127.0.0.1:8381, which is published
// only by docker-compose.dev.yml (docker compose -f docker-compose.yml -f docker-compose.dev.yml up).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8381' },
  },
});
