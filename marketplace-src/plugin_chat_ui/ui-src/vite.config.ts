import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/postcss'
import path from 'node:path'
import fs from 'node:fs'

// Builds the chat panel as an IIFE with react/react-dom as EXTERNALS mapped
// to the shell's window.LunaReact* globals (set in luna ui/src/main.tsx), so
// the page runs exactly one React instance and the component mounts natively
// in the shell's tree. Output: ../ui/chat.js + ../ui/chat.css, served by
// routes.py at /api/p/plugin-chat-ui/ui/.
//
// Sources are NOT vendored: `@luna/*` resolves into the live luna core UI
// tree (../../../luna/ui/src). ChatPanel and its chat-only helpers live here
// under src/views/; everything shared (api, stores, components) is imported
// from core so the plugin can never drift from the shell it mounts into.

const LUNA_UI_SRC = path.resolve(__dirname, '../../../luna/ui/src')

// Same stamp core's ui/vite.config.ts injects — @luna/lib/cache reads it.
function lunaVersion(): string {
  try {
    const init = fs.readFileSync(
      path.resolve(__dirname, '../../../luna/luna/__init__.py'), 'utf8')
    return /__version__\s*=\s*"([^"]+)"/.exec(init)?.[1] ?? 'dev'
  } catch {
    return 'dev'
  }
}

export default defineConfig(() => {
  // Pin bare-module deps imported from the @luna tree to THIS package's
  // node_modules — the importer sits in luna/ui/src, so node's walk-up would
  // otherwise pick luna/ui/node_modules and the build stops being
  // deterministic w.r.t. this package's lockfile.
  const depPins: Record<string, string> = {}
  for (const dep of [
    '@microsoft/fetch-event-source', 'clsx', 'lucide-react',
    'react-markdown', 'remark-gfm', 'tailwind-merge', 'zustand',
  ]) depPins[dep] = path.resolve(__dirname, 'node_modules', dep)

  // Under vitest there is no externalization, so react itself must also be
  // pinned or @luna files would load a SECOND react from luna/ui/node_modules
  // and hooks break. Never pin react for the lib build — it must stay
  // external ('react' the bare specifier is what rollupOptions.external and
  // output.globals match on).
  const reactPins: Record<string, string> = process.env.VITEST
    ? {
        'react/jsx-runtime': path.resolve(__dirname, 'node_modules/react/jsx-runtime'),
        'react/jsx-dev-runtime': path.resolve(__dirname, 'node_modules/react/jsx-dev-runtime'),
        'react-dom/client': path.resolve(__dirname, 'node_modules/react-dom/client'),
        'react-dom': path.resolve(__dirname, 'node_modules/react-dom'),
        react: path.resolve(__dirname, 'node_modules/react'),
      }
    : {}

  return {
    plugins: [react()],
    css: {
      postcss: {
        plugins: [tailwindcss()],
      },
    },
    define: {
      // Production only for the shipped IIFE — under vitest this must stay
      // unset so @testing-library gets development React (React.act).
      ...(process.env.VITEST
        ? {}
        : { 'process.env.NODE_ENV': JSON.stringify('production') }),
      'import.meta.env.VITE_LUNA_VERSION': JSON.stringify(lunaVersion()),
    },
    resolve: {
      alias: {
        '@luna': LUNA_UI_SRC,
        ...depPins,
        ...reactPins,
      },
    },
    build: {
      outDir: '../ui',
      emptyOutDir: true,
      sourcemap: false,
      cssCodeSplit: false,
      lib: {
        entry: 'src/entry.tsx',
        name: 'LunaChatUI',
        formats: ['iife'],
        fileName: () => 'chat.js',
      },
      rollupOptions: {
        external: ['react', 'react-dom', 'react-dom/client', 'react/jsx-runtime'],
        output: {
          globals: {
            react: 'LunaReact',
            'react-dom': 'LunaReactDOM',
            'react-dom/client': 'LunaReactDOM',
            'react/jsx-runtime': 'LunaReactJsxRuntime',
          },
          assetFileNames: (info) =>
            info.name?.endsWith('.css') ? 'chat.css' : '[name][extname]',
        },
      },
    },
  }
})
