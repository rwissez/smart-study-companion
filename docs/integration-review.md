# 联调审阅指南

这个分支供 A 审阅。仓库使用合成示例。赛方资料、教师复核成果、运行数据库和模型配置保存在本机。

## 快速预览

使用 Node.js 22.12 或更新的 22.x 版本。在 `frontend` 运行 `npm ci` 和 `npm run dev`，打开 http://127.0.0.1:5173/ 。默认用合成示例，可以查看班级对比、学生画像、证据面板、建议和学生端。

## 联调验证

需要 Python 3.12、uv 和 Node.js。在仓库根目录运行：

```powershell
uv sync --locked
uv run alembic upgrade head
uv run study-companion seed-demo
uv run uvicorn study_companion.main:app --host 127.0.0.1 --port 8000
```

另开 PowerShell，在 `frontend` 运行：

```powershell
npm ci
$env:VITE_DATA_SOURCE = "api"
npm run dev
```

以教师身份进入，打开合成学生的画像，生成建议并尝试修改、审核发布和导出。学生身份只看已发布建议。正式联调的数据可在本机通过「数据管理」导入六张 CSV。

导入时可通过本机 `data/course-review.json` 增补人工复核的教材出处和教学联系。文件有两个可选字段。`textbook_refs` 按观察指标编号保存出处数组，每条含 `source_file`、`pdf_page`、`verification_status`。`links` 保存由 `source_id`、`target_id`、`relation`、`basis` 和 `description` 组成的教学联系。`relation` 可为 `supports` 或 `prerequisite`。配置保存在 Git 忽略的 `data/`。

## 审阅重点

- 核对新纳入的测验范围、选择题选项、评分点和班级覆盖口径。
- 查看建议生成、教师修订、学生可见版本及教师导出。共享字段与生成契约由 A 统一审阅和更新。
- 模型默认为空、真实模型与生成内容合规待验收。学情摘要只在用户主动选择时交给本机模型。
- 后端用 `uv run pytest -q` 检查。前端用 `npm run typecheck`、`npm run test -- --run` 和 `npm run build` 检查。
