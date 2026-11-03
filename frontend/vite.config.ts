import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// El build va directo a donde FastAPI lo sirve (jobhunt/web/dist). Sin CDN, sin inline: la CSP
// de la SPA es script-src 'self'; style-src 'self'.
export default defineConfig({
  plugins: [svelte()],
  base: '/',
  build: {
    outDir: '../jobhunt/web/dist',
    emptyOutDir: true,
    target: 'es2022',
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: { manualChunks: (id: string) => (/node_modules\/(echarts|zrender)\//.test(id) ? 'echarts' : undefined) },
    },
  },
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8787', '/login': 'http://127.0.0.1:8787' } },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
});
