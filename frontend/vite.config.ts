import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

// Allow E2E tests to point the proxy at a different backend port (e.g. 8099)
// without changing the default dev port of 8000.
const backendPort = process.env.VITE_BACKEND_PORT ?? '8000';
const proxyTarget = `http://localhost:${backendPort}`;
const proxy = {
  '/api': { target: proxyTarget, changeOrigin: true },
  '/auth': { target: proxyTarget, changeOrigin: true },
};

export default defineConfig({
  plugins: [
    tailwindcss(),
    sveltekit()
  ],
  server: {
    port: 5199,
    proxy,
  },
  preview: {
    // vite preview does not apply server.proxy — must be set explicitly here.
    proxy,
  },
});
