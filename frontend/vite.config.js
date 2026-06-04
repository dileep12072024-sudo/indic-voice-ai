import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  // Backend target for local dev — override with VITE_DEV_BACKEND in .env
  const backendTarget = env.VITE_DEV_BACKEND || 'http://localhost:8000'

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        // ── BUG-1 FIX ──────────────────────────────────────────────────────
        // Previously only /api was proxied, so POST /generate and
        // GET /job/:id called port 5173 (Vite) instead of 8000 (FastAPI).
        // All backend routes are now proxied in local dev.
        // Production traffic goes through VITE_WORKERS_URL / VITE_API_BASE.
        '/generate': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/job': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/jobs': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/health': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/files': {
          target: backendTarget,
          changeOrigin: true,
        },
        // Legacy /api prefix — kept for any tooling that still uses it
        '/api': {
          target: backendTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, '')
        }
      }
    },
    build: {
      outDir: 'dist',
      sourcemap: false,
      chunkSizeWarningLimit: 1000,
      rollupOptions: {
        output: {
          manualChunks: {
            vendor: ['react', 'react-dom']
          }
        }
      }
    },
    define: {
      __APP_VERSION__: JSON.stringify(process.env.npm_package_version || '0.0.0')
    }
  }
})
