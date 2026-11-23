# 智慧学伴前端（D 模块）

双端界面包含教师工作台与学生学习空间。本地联调副本支持使用示例或连接本地后端，详见上级目录的 [联调审阅指南](../docs/integration-review.md)。

## 运行

需要 Node.js 22.x（至少 22.12），使用 npm。

```powershell
cd frontend
npm ci
npm run dev
```

浏览器打开 http://127.0.0.1:5173 ，页面上会看到身份选择。

首次运行如需改数据来源，把 `.env.example` 复制成 `.env`：

```powershell
Copy-Item .env.example .env
```

`.env` 里只有一项 `VITE_DATA_SOURCE`：

- `demo`（默认）使用 `src/api/demo/fixtures/` 的固定示例。
- `api` 请求本地 `/api/v1`，可上传六表、查看学情并在教师端生成规则建议。建议来源待核对时标记为待复核。

## 检查命令

```powershell
npm run typecheck   # 类型检查
npm run build       # 类型检查 + 生产构建
npm run test -- --run   # 示例服务与数据回归测试
npm run test:e2e        # 另一个终端启动 dev 后，用本机 Edge 跑关键浏览器流程
```

## 目录

```text
src/
  app/        路由、Providers、会话、范围（数据集／运行／节点）
  api/        接口封装、临时类型、示例服务与 fixtures
  components/ 反馈、范围条、学情组件、证据面板、对话视图
  layouts/    双端壳层
  pages/      九个功能入口
  styles/     设计变量、全局样式、通用组件样式
```

## 三条不能破的规矩

1. 前端不重算统计，只显示接口给的分子、分母和数值。
2. `null` 显示「无可用结果」，有效的 0 分显示 0，两者不能混。
3. 示例数据的页面必须有「示例数据」标记，不能冒充真实学生结果。

接口字段的临时定义在 `src/api/types.ts`，字段逐条对应团队手册 08 与 11。
A 交付可机读契约后，这个文件由生成脚本替换，页面不改。

浏览器流程检查班级返回、学生上下文、证据抽屉、建议版本、补充观察快照及学生提示。可通过 `BROWSER_EXECUTABLE` 指定本机 Chromium，通过 `USE_BUILT_FILES=1` 使用已经构建的 `dist/` 文件。指定截图目录时使用 `npm run test:e2e -- 截图目录`。
