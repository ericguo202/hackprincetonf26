/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Matterport Showcase SDK key. Not a secret. */
  readonly VITE_MATTERPORT_SDK_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
