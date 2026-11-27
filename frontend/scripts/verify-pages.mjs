/**
 * 页面自检：真实前端开发服务器 + 真实后端。
 *
 * 用法：node scripts/verify-pages.mjs [输出目录]
 * 前置：npm run dev（默认 http://127.0.0.1:5173）
 *       后端 uv run uvicorn study_companion.main:app --port 8000
 * 环境：BASE_URL、VITE_API_BASE_URL、BROWSER_EXECUTABLE
 *
 * 任何前置不满足、页面断言失败或控制台报错都会以非 0 退出并输出原因，不会假装成功。
 */
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.resolve(HERE, "..");
const REPO = path.resolve(FRONTEND, "..");
const OUT = path.resolve(process.argv[2] ?? path.join(REPO, "docs", "evidence", "ui"));
const BASE = (process.env.BASE_URL ?? "http://127.0.0.1:5173").replace(/\/+$/, "");
const API = (process.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000/api/v1").replace(/\/+$/, "");
const FIXTURES = path.join(REPO, "tests", "fixtures", "six_tables");

function fail(message) {
  console.error(`\n失败：${message}`);
  process.exit(1);
}

async function preflight() {
  try {
    const response = await fetch(`${BASE}/`);
    if (!response.ok) fail(`前端开发服务器返回 HTTP ${response.status}（${BASE}）`);
  } catch {
    fail(`连不上前端开发服务器 ${BASE}。请先在 frontend/ 运行 npm run dev。`);
  }
  let health;
  try {
    const response = await fetch(`${API}/health`);
    if (!response.ok) fail(`后端健康检查返回 HTTP ${response.status}（${API}/health）`);
    const payload = await response.json();
    health = payload?.data ?? payload;
  } catch {
    fail(
      `连不上后端 ${API}。请先在仓库根目录运行：\n` +
        "  uv run alembic upgrade head\n" +
        "  uv run uvicorn study_companion.main:app --host 127.0.0.1 --port 8000\n" +
        "  uv run python _local/seed_local.py",
    );
  }
  if (health?.database_ok !== true) fail("后端健康检查显示数据库不可用。");
  if (health?.demo_mode !== true) fail("后端演示身份入口未开启（DEMO_MODE 不是 true），身份入口无法创建会话。");
  console.log("preflight ok:", { frontend: BASE, api: API, schema: health.schema_version });
}

async function launchBrowser() {
  if (process.env.BROWSER_EXECUTABLE) {
    return chromium.launch({ executablePath: process.env.BROWSER_EXECUTABLE, headless: true });
  }
  try {
    return await chromium.launch({ channel: "msedge", headless: true });
  } catch {
    return chromium.launch({ channel: "chrome", headless: true });
  }
}

await preflight();
await mkdir(OUT, { recursive: true });

let browser;
try {
  browser = await launchBrowser();
} catch (error) {
  fail(
    `无法启动 Edge/Chrome：${String(error)}\n` +
      "请设置 BROWSER_EXECUTABLE 指向本机浏览器，例如\n" +
      "  $env:BROWSER_EXECUTABLE='C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'",
  );
}

const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
page.setDefaultTimeout(20000);
const consoleErrors = [];
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
page.on("pageerror", (error) => consoleErrors.push(String(error)));

let shotIndex = 0;
const snap = async (label) => {
  shotIndex += 1;
  const name = `${String(shotIndex).padStart(2, "0")}-${label}`;
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
  console.log("ok", name);
};

const noOverflow = async (label) => {
  const size = await page.evaluate(() => ({ width: window.innerWidth, scroll: document.documentElement.scrollWidth }));
  assert.ok(size.scroll <= size.width + 1, `${label} 出现横向溢出：scrollWidth=${size.scroll} viewport=${size.width}`);
  console.log("ok 无横向溢出", label);
};

const noProtectedFields = async (label) => {
  const text = await page.locator("body").innerText();
  for (const field of ["reference_answer", "reference_explanation", "rubric_points"]) {
    assert.ok(!text.includes(field), `${label} 出现了受保护字段 ${field}`);
  }
  console.log("ok 无受保护字段", label);
};

const nav = async (name) => {
  await page.getByRole("link", { name, exact: true }).first().click();
};

const activityRow = (text) => page.locator(".activityRow").filter({ hasText: text }).first();
const microLessonRow = (title) =>
  page.locator(".activityRow").filter({ hasText: "微课堂" }).filter({ hasText: title }).first();

try {
  // ---------------------------------------------------------------- 身份入口
  await page.goto(`${BASE}/identity`, { waitUntil: "networkidle" });
  await page.getByRole("heading", { name: "选择身份" }).waitFor();
  await page.getByLabel("数据集").waitFor();
  await snap("identity-1440");
  await noOverflow("身份入口 1440");

  // ---------------------------------------------------------------- 教师端
  await page.getByRole("button", { name: /^教师/ }).first().click();
  await page.getByRole("button", { name: /以教师身份进入/ }).click();
  await page.waitForURL(/\/teacher\/classes/);
  await page.getByRole("heading", { name: "班级总览" }).waitFor();
  await page.getByText("名册人数", { exact: true }).waitFor();
  await page.getByText("有效观测学生", { exact: true }).waitFor();
  await snap("teacher-classes-1440");
  await noOverflow("班级总览 1440");

  await page.getByRole("button", { name: "班级比较" }).click();
  await page.getByRole("heading", { name: "班级比较" }).waitFor();
  await page.getByRole("button", { name: "比较这两个班" }).click();
  await page.getByText(/两班口径|口径一致/).waitFor();
  await snap("teacher-compare-1440");
  await page.getByRole("button", { name: "返回班级视图" }).click();
  await page.getByText("名册人数", { exact: true }).waitFor();

  await page.getByRole("button", { name: "查看画像" }).first().click();
  await page.waitForURL(/\/teacher\/students\//);
  await page.getByText("诊断发现", { exact: true }).waitFor();
  await page.getByText("观察指标").first().waitFor();
  await snap("teacher-profile-overview-1440");
  await page.getByText("学习方案摘要", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "编辑学习方案" }).isDisabled(), true, "方案编辑入口占位应该是禁用状态");
  await noProtectedFields("教师画像");

  await page.getByRole("tab", { name: "进展" }).click();
  await page.getByRole("heading", { name: "学习进展" }).waitFor();
  await snap("teacher-profile-progress-1440");

  await page.getByRole("tab", { name: "作答记录" }).click();
  await page.getByRole("heading", { name: "作答记录" }).waitFor();
  await snap("teacher-profile-records-1440");

  await page.getByRole("tab", { name: "学习方案" }).click();
  await page.getByText("任务组 1").first().waitFor();
  await snap("teacher-profile-plan-1440");

  await page.getByRole("tab", { name: "概览" }).click();
  await page.getByRole("button", { name: /查看依据/ }).first().click();
  const drawer = page.getByRole("dialog", { name: "证据详情" });
  await drawer.waitFor();
  await drawer.getByText(/证据|指标|作答记录/).first().waitFor();
  await snap("evidence-drawer-teacher-1440");
  await page.keyboard.press("Escape");
  await drawer.waitFor({ state: "hidden" });

  await page.getByRole("button", { name: "返回班级" }).click();
  await page.waitForURL(/\/teacher\/classes/);
  await page.getByText("名册人数", { exact: true }).waitFor();

  await nav("课程体系");
  await page.getByRole("heading", { name: "课程体系" }).waitFor();
  await page.getByText("主题与指标", { exact: true }).waitFor();
  await snap("teacher-curriculum-1440");
  await noOverflow("课程体系 1440");

  await page.getByRole("button", { name: "审核", exact: true }).click();
  await page.getByText("异常与审核", { exact: true }).waitFor();
  await snap("teacher-curriculum-reviews-1440");

  // ------------------------------------------------------- 教师端多分辨率（数据已就绪）
  await page.setViewportSize({ width: 1920, height: 1080 });
  await nav("班级总览");
  await page.getByText("名册人数", { exact: true }).waitFor();
  await noOverflow("班级总览 1920");
  await snap("teacher-classes-1920");
  await nav("课程体系");
  await page.getByText("主题与指标", { exact: true }).waitFor();
  await noOverflow("课程体系 1920");
  await snap("teacher-curriculum-1920");
  await nav("数据管理");
  await page.getByText("当前数据范围", { exact: true }).waitFor();
  await noOverflow("数据管理 1920");
  await snap("teacher-data-1920");
  await nav("AI 对话");
  await page.getByText("模型服务尚未配置").first().waitFor();
  await noOverflow("AI 对话 1920");
  await snap("teacher-chat-1920");

  await page.setViewportSize({ width: 1366, height: 768 });
  await nav("班级总览");
  await page.getByText("名册人数", { exact: true }).waitFor();
  await noOverflow("班级总览 1366");
  await snap("teacher-classes-1366");
  await nav("课程体系");
  await page.getByText("主题与指标", { exact: true }).waitFor();
  await noOverflow("课程体系 1366");
  await snap("teacher-curriculum-1366");
  await nav("数据管理");
  await page.getByText("当前数据范围", { exact: true }).waitFor();
  await noOverflow("数据管理 1366");
  await snap("teacher-data-1366");
  await nav("AI 对话");
  await page.getByText("模型服务尚未配置").first().waitFor();
  await noOverflow("AI 对话 1366");
  await snap("teacher-chat-1366");
  await page.setViewportSize({ width: 1440, height: 900 });

  // ---------------------------------------------------------------- 数据管理
  await nav("数据管理");
  await page.getByRole("heading", { name: "数据管理" }).waitFor();
  await page.getByText("当前数据范围", { exact: true }).waitFor();
  await snap("teacher-data-dataset-1440");

  const files = ["requirements", "materials", "students", "nodes", "records", "dictionary"].map((name) =>
    path.join(FIXTURES, `${name}.csv`),
  );
  await page.locator('input[type="file"]').setInputFiles(files);
  await page.getByRole("button", { name: "上传并开始处理" }).click();
  await page.getByRole("heading", { name: "导入回执" }).waitFor({ timeout: 60000 });
  await snap("teacher-data-upload-receipt-1440");
  const switchToNew = page.getByRole("button", { name: "切换数据集并查看任务" });
  if (await switchToNew.count()) {
    await switchToNew.click();
    await page.getByRole("alertdialog", { name: "切换到新导入的数据集" }).waitFor();
    await page.getByRole("button", { name: "切换并重建会话" }).click();
    await page.getByText("阶段：全部完成").waitFor({ timeout: 90000 });
    await snap("teacher-data-jobs-1440");
  } else {
    const progressLink = page.getByRole("button", { name: "查看任务进度" });
    if (await progressLink.count()) {
      await progressLink.click();
      await page.getByText(/任务进度/).waitFor();
      await page.getByText("阶段：全部完成").waitFor({ timeout: 90000 });
      await snap("teacher-data-jobs-1440");
    }
  }

  await page.getByRole("tab", { name: "质量" }).click();
  await page.getByRole("heading", { name: "质量报告" }).waitFor();
  await page.getByText("数据质量", { exact: true }).waitFor();
  await snap("teacher-data-quality-1440");

  await page.getByRole("tab", { name: "成果导出" }).click();
  const exportButton = page.getByRole("button", { name: "生成导出" });
  if (await exportButton.count()) {
    await exportButton.click();
    await page.getByText("下载 ZIP").first().waitFor({ timeout: 90000 });
  } else {
    await page.getByText("当前会话没有导出权限").waitFor();
  }
  await snap("teacher-data-exports-1440");

  const datasetSelect = page.locator("header select").first();
  const options = await datasetSelect
    .locator("option")
    .evaluateAll((nodes) => nodes.map((node) => node.value).filter(Boolean));
  const current = await datasetSelect.inputValue();
  const other = options.find((value) => value !== current);
  if (other) {
    await datasetSelect.selectOption(other);
    await page.getByRole("alertdialog", { name: "切换数据集" }).waitFor();
    await snap("dataset-switch-confirm-1440");
    await page.getByRole("button", { name: "取消", exact: true }).click();
    await page.getByRole("alertdialog", { name: "切换数据集" }).waitFor({ state: "hidden" });
  }

  await nav("AI 对话");
  await page.getByText("模型服务尚未配置").first().waitFor();
  await page.getByText("会话历史", { exact: true }).waitFor();
  await snap("teacher-chat-1440");
  await page.getByRole("button", { name: "配置模型" }).click();
  await page.getByRole("dialog", { name: "模型设置" }).waitFor();
  await snap("teacher-model-settings-1440");
  await page.getByRole("dialog", { name: "模型设置" }).getByRole("button", { name: "关闭", exact: true }).last().click();

  await page.getByRole("button", { name: /退出并重选身份/ }).first().click();
  await page.waitForURL(/\/identity/);
  await page.getByRole("heading", { name: "选择身份" }).waitFor();
  await page.getByRole("button", { name: /^教师/ }).first().waitFor();

  // ---------------------------------------------------------------- 学生端
  await page.getByRole("button", { name: /^学生/ }).first().click();
  await page.getByRole("button", { name: /S01/ }).first().click();
  await page.getByRole("button", { name: /以学生 S01 身份进入/ }).click();
  await page.waitForURL(/\/student\/profile/);
  await page.getByRole("heading", { name: "个人画像" }).waitFor();
  await page.getByText("学习表现", { exact: true }).waitFor();
  await snap("student-profile-1440");
  await noOverflow("学生个人画像 1440");
  await noProtectedFields("学生个人画像");

  await page.setViewportSize({ width: 1920, height: 1080 });
  await noOverflow("学生个人画像 1920");
  await snap("student-profile-1920");
  await page.setViewportSize({ width: 1366, height: 768 });
  await noOverflow("学生个人画像 1366");
  await snap("student-profile-1366");
  await page.setViewportSize({ width: 1440, height: 900 });

  await nav("学习计划");
  await page.getByRole("heading", { name: "学习计划" }).waitFor();
  await page.getByText("任务组 1").first().waitFor();
  await snap("student-plan-1440");
  await noOverflow("学习计划 1440");

  await page.setViewportSize({ width: 1920, height: 1080 });
  await noOverflow("学习计划 1920");
  await snap("student-plan-1920");
  await page.setViewportSize({ width: 1366, height: 768 });
  await noOverflow("学习计划 1366");
  await snap("student-plan-1366");
  await page.setViewportSize({ width: 1440, height: 900 });

  // 微课堂：六类组件渲染与专用完成
  await microLessonRow("去括号时的符号处理").getByRole("button", { name: /开始|继续/ }).click();
  await page.getByRole("heading", { name: "去括号时的符号处理", level: 1 }).waitFor();
  await page.getByText("引用", { exact: true }).waitFor();
  await noProtectedFields("微课堂");
  await snap("student-micro-lesson-1440");
  await page.setViewportSize({ width: 1920, height: 1080 });
  await noOverflow("微课堂 1920");
  await snap("student-micro-lesson-1920");
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: "完成微课堂" }).click();
  await page.getByText("已完成", { exact: true }).first().waitFor();
  await snap("student-micro-lesson-completed-1440");
  await page.getByRole("button", { name: "返回学习计划" }).click();
  await page.getByText("任务组 1").first().waitFor();

  // 选择题：真实选项 + 三级提示 + 评分
  await activityRow("化简").getByRole("button", { name: /开始|继续/ }).click();
  await page.waitForURL(/\/student\/plan\/.+\/activities\//);
  await page.getByText("化简 -(2x-7) 的结果是（）", { exact: true }).waitFor();
  await noProtectedFields("选择题作答");
  await page.getByRole("radio").nth(1).check();
  await snap("student-activity-choice-1440");

  await page.getByRole("button", { name: "获取第 1 级提示" }).click();
  await page.getByRole("alertdialog", { name: "获取帮助前确认" }).waitFor();
  await snap("student-hint-confirm-1440");
  await page.getByRole("button", { name: "继续获取帮助" }).click();
  await page.getByText("提示 1", { exact: true }).waitFor();
  await snap("student-hint-released-1440");

  await page.getByRole("button", { name: "提交作答" }).click();
  await page.getByText("5 / 5").first().waitFor();
  await page.getByText("已评分").first().waitFor();
  await snap("student-activity-graded-1440");

  await page.getByRole("button", { name: "返回学习计划" }).click();
  await page.getByText("任务组 1").first().waitFor();

  // 解答题：没有模型时如实待复核，不显示 0 分
  await activityRow("解方程 2(x-3)+1=5").getByRole("button", { name: /开始|继续/ }).click();
  await page.getByText("解题过程", { exact: true }).waitFor();
  await page.getByLabel("解题过程").fill("去括号得 2x-6+1=5，2x=10，x=5。检验左边 2(5-3)+1=5。");
  await page.getByLabel("最终答案").fill("x=5");
  await page.getByRole("button", { name: "提交作答" }).click();
  await page.getByText("已提交，待评分/待复核").waitFor();
  const pendingText = await page.locator("body").innerText();
  assert.ok(!pendingText.includes("0 / 5"), "解答题待复核时不应显示 0 分");
  await snap("student-activity-solution-pending-1440");
  await page.getByRole("button", { name: "返回学习计划" }).click();
  await page.getByText("任务组 1").first().waitFor();

  // 填空题
  await activityRow("解方程 3x+8=20").getByRole("button", { name: /开始|继续/ }).click();
  await page.getByLabel("填写答案").waitFor();
  await page.getByLabel("填写答案").fill("4");
  await snap("student-activity-fillblank-1440");
  await page.getByRole("button", { name: "提交作答" }).click();
  await page.getByText("5 / 5").first().waitFor();
  await page.getByRole("button", { name: "返回学习计划" }).click();
  await page.getByText("任务组 1").first().waitFor();

  // 已完成活动：历史状态
  await activityRow("化简").getByRole("button", { name: "查看" }).click();
  await page.getByText("该活动已完成", { exact: true }).waitFor();
  await snap("student-activity-history-1440");
  await page.getByRole("button", { name: "返回学习计划" }).click();
  await page.getByText("任务组 1").first().waitFor();

  // ------------------------------------------------------------ 学生对话与证据
  await nav("AI 对话");
  await page.getByText("会话历史", { exact: true }).waitFor();
  await snap("student-chat-1440");
  await page.locator("#chat-input").fill("我这次先练什么比较好？");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await page.getByText("规则摘要（未使用模型）").first().waitFor({ timeout: 60000 });
  await snap("student-chat-rule-summary-1440");
  await page.setViewportSize({ width: 1920, height: 1080 });
  await noOverflow("学生对话 1920");
  await snap("student-chat-1920");
  await page.setViewportSize({ width: 1366, height: 768 });
  await noOverflow("学生对话 1366");
  await snap("student-chat-1366");
  await page.setViewportSize({ width: 1440, height: 900 });

  await nav("个人画像");
  await page.getByText("学习表现", { exact: true }).waitFor();
  await page.getByRole("button", { name: "查看依据" }).first().click();
  const studentDrawer = page.getByRole("dialog", { name: "证据详情" });
  await studentDrawer.waitFor();
  await studentDrawer.getByText(/证据|指标|作答记录/).first().waitFor();
  await snap("student-evidence-1440");
  await page.keyboard.press("Escape");
  await studentDrawer.waitFor({ state: "hidden" });

  await page.getByRole("button", { name: /退出并重选身份/ }).first().click();
  await page.waitForURL(/\/identity/);
  await page.getByRole("heading", { name: "选择身份" }).waitFor();
  await page.setViewportSize({ width: 1920, height: 1080 });
  await noOverflow("身份入口 1920");
  await snap("identity-1920");
  await page.setViewportSize({ width: 1366, height: 768 });
  await noOverflow("身份入口 1366");
  await snap("identity-1366");

  await browser.close();
} catch (error) {
  try {
    await page.screenshot({ path: path.join(OUT, "_failure.png"), fullPage: true });
  } catch {
    // 截图只用于排查，失败原因以原异常为准。
  }
  await browser.close();
  fail(error instanceof Error ? `${error.message}\n${error.stack ?? ""}` : String(error));
}

if (consoleErrors.length > 0) {
  console.error("\n浏览器控制台错误：");
  for (const line of consoleErrors) console.error(" -", line);
  fail("页面存在控制台错误或未捕获异常。");
}

console.log(`\n页面自检通过，截图目录：${OUT}`);
