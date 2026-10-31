import { defineConfig } from 'vite';
export default defineConfig({
  root: 'web',
  build: { outDir: '../dist/web', emptyOutDir: true },
  plugins: [
    {
      name: 'app-preview-routes',
      configurePreviewServer(server) {
        server.middlewares.use((req, _res, next) => {
          const url = new URL(req.url || '/', 'http://localhost');
          if (
            ['/stats', '/link', '/app'].includes(url.pathname) ||
            url.pathname.startsWith('/app/')
          )
            req.url = '/app/index.html';
          next();
        });
      },
    },
  ],
});
