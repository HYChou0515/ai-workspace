/// <reference types="vite/client" />

/** Build-time app version (vite `define`, from pyproject.toml) — the FE half
 * of the version-skew handshake. Empty string when built without a pyproject. */
declare const __APP_VERSION__: string;

interface ImportMetaEnv {
  readonly VITE_USE_MOCK?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
