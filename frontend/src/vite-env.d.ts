/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 数据来源：demo（默认，浏览器内固定示例）或 api（连本地后端） */
  readonly VITE_DATA_SOURCE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
