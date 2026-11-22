import path from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const projectRoot = path.resolve(import.meta.dirname, '..')
const legacyWebRoot = path.resolve(projectRoot, 'web-legacy')
const compatRoot = path.resolve(import.meta.dirname, 'src/compat')
const apiProxy = {
  target: process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:5001',
  changeOrigin: false,
}

export default defineConfig(({ mode }) => ({
  base: '/',
  publicDir: path.resolve(legacyWebRoot, 'public'),
  plugins: [react()],
  define: {
    'process.env.NODE_ENV': JSON.stringify(mode),
  },
  server: {
    host: '0.0.0.0',
    port: 3100,
    strictPort: false,
    proxy: {
      '/console/api': apiProxy,
      '/api': apiProxy,
      '/triggers': apiProxy,
    },
  },
  preview: {
    host: '0.0.0.0',
    port: 3100,
    proxy: {
      '/console/api': apiProxy,
      '/api': apiProxy,
      '/triggers': apiProxy,
    },
  },
  css: {
    postcss: path.resolve(legacyWebRoot, 'postcss.config.mjs'),
  },
  resolve: {
    dedupe: ['react', 'react-dom'],
    alias: [
      { find: /^@\/next\/navigation$/, replacement: path.resolve(compatRoot, 'navigation.ts') },
      { find: /^next\/navigation$/, replacement: path.resolve(compatRoot, 'navigation.ts') },
      { find: /^@\/next\/link$/, replacement: path.resolve(compatRoot, 'link.tsx') },
      { find: /^next\/link$/, replacement: path.resolve(compatRoot, 'link.tsx') },
      { find: /^next\/dynamic$/, replacement: path.resolve(compatRoot, 'dynamic.tsx') },
      { find: /^@\/next\/script$/, replacement: path.resolve(compatRoot, 'script.tsx') },
      { find: /^next\/script$/, replacement: path.resolve(compatRoot, 'script.tsx') },
      { find: /^@\/next\/headers$/, replacement: path.resolve(compatRoot, 'headers.ts') },
      { find: /^next\/headers$/, replacement: path.resolve(compatRoot, 'headers.ts') },
      { find: /^server-only$/, replacement: path.resolve(compatRoot, 'server-only.ts') },
      { find: /^next-themes$/, replacement: path.resolve(compatRoot, 'theme.tsx') },
      { find: /^~@\//, replacement: `${legacyWebRoot}/` },
      { find: '@', replacement: legacyWebRoot },
      { find: '#i18n', replacement: path.resolve(legacyWebRoot, 'i18n/lib.client.ts') },
    ],
  },
  fs: {
    allow: [projectRoot],
  },
}))
