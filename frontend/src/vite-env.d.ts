/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 覆盖 API 根地址；留空时使用同源（开发环境由 Vite 代理 /api）。 */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
