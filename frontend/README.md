# 湛知 Web 前端

React 18 + TypeScript + Vite。教师 4 页、学生 3 页与身份入口，样式只引用
`docs/formal-design/visual-tokens.css` 的 token（复制在 `src/styles/visual-tokens.css`）。

## 运行

```bash
# 1. 后端（仓库根目录）
uv run alembic upgrade head
uv run uvicorn study_companion.main:app --host 127.0.0.1 --port 8000
uv run python _local/seed_local.py        # 可选：导入合成六表数据

# 2. 前端
cd frontend
npm install
npm run dev                               # http://127.0.0.1:5173
```

数据来源由 `VITE_DATA_SOURCE` 控制：

* `api`（默认）：只访问 `VITE_API_BASE_URL`，失败显示可读错误，**不会**回退演示数据；
* `demo`：显式设定后才启用浏览器内演示数据，页面右上与侧栏显示“演示数据”角标；
* 其他值：显示配置错误页。

`VITE_API_BASE_URL` 默认 `http://127.0.0.1:8000/api/v1`。

## 质量命令

```bash
npm run typecheck        # tsc --noEmit
npm run build            # tsc --noEmit && vite build
npm test -- --run        # vitest
node scripts/verify-pages.mjs   # 真实前端 + 真实后端页面自检，截图到 docs/evidence/ui/
```

页面自检脚本要求前端 `npm run dev` 与后端 8000 端口都在运行；`BROWSER_EXECUTABLE`
可指定本机 Edge/Chrome，默认尝试 msedge / chrome 通道。后端未启动时脚本会直接失败并说明原因，
不会输出“成功”。

## 目录

```
src/
  api/            唯一 HTTP 入口（client 统一包络与错误，service 唯一接口面，demo 仅演示）
  app/            路由、会话（token 只在内存）、查询 Provider
  components/     证据抽屉、雷达、任务组、作答、任务进度、对话、模型设置、确认弹窗
  layouts/        208px 侧栏 + 64px 顶栏壳层
  lib/            queryKey（mode/epoch/dataset/…/version/page）、格式化、活动状态推导、草稿隔离
  pages/          身份入口、教师 4 页、学生 3 页与活动页
```
