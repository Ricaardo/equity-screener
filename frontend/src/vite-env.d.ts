/// <reference types="vite/client" />

interface ImportMetaEnv {
  // Base URL for live report JSON (e.g. a raw.githubusercontent.com/<repo>/<branch>
  // path). When unset, the app falls back to the static snapshot baked under
  // /api/report/* at build time.
  readonly VITE_DATA_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
