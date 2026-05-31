import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // Dev-only proxy: frontend on :5173 hits backend on :8000.
    // In production the FastAPI container serves the built `dist/` itself
    // and the proxy is unused.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    // Match FRONTEND_DIST in web/backend/app.py.
    outDir: 'dist',
    assetsDir: 'assets',
  },
});
