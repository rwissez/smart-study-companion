/**
 * 真实数据集页面自检（40 名学生 / 2 个班，ds-5e7afca301f8410387d7，学生 D01—D40）。
 * 前置：npm run dev + 后端 8000；失败会明确报错，不会假成功。
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
const DATASET_ID = "ds-5e7afca301f8410387d7";

function fail(message) {
  console.error(`\n失败：${message}`);
  process.exit(1);
}

async function preflight() {
  try {
    if (!(await fetch(`${BASE}/`)).ok) fail(`前端不可用：${BASE}`);
  } catch {
    fail(`连不上前端 ${BASE}，请先 npm run dev。`);
  }
  try {
    const health = await (await fetch(`${API}/health`)).json();
    if (health?.data?.database_ok !== true) fail("后端数据库不可用。");
    const session = await (await fetch(`${API}/demo-sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: "teacher" }),
    })).json();
    const token = session?.data?.session_token;
    if (!token) fail("无法创建后端 bootstrap 会话。");
    const datasets = await (await fetch(`${API}/datasets`, {
      headers: { Authorization: `Bearer ${token}` },
    })).json();
    const ids = (datasets?.data?.items ?? []).map((item) => item.dataset_id);
    if (!ids.includes(DATASET_ID)) fail(`真实数据集 ${DATASET_ID} 不在后端；请先导入 40 名学生数据。`);
    console.log("preflight ok:", { frontend: BASE, api: API, dataset: DATASET_ID });
  } catch (error) {
    fail(`后端预检失败：${String(error)}`);
  }
}

async function seedCandidate() {
  const session = await (await fetch(`${API}/demo-sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role: "teacher", dataset_id: DATASET_ID }),
  })).json();
  const headers = { Authorization: `Bearer ${session.data.session_token}`, "Content-Type": "application/json" };
  const runs = await (await fetch(`${API}/datasets/${DATASET_ID}/runs`, { headers })).json();
  const run = runs?.data?.items?.find((item) => item.status === "succeeded") ?? runs?.data?.items?.[0];
  if (!run) fail("真实数据集没有正式分析运行，不能生成候选。");
  const chat = await fetch(`${API}/chat`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      messages: [{ role: "user", content: "看看 DC01 班级情况" }],
      task_type: "class_analysis",
      node_id: run.node_ids[0],
      run_id: run.run_id,
      target: { type: "class", id: "DC01" },
    }),
  });
  if (!chat.ok) fail(`创建教师会话失败：HTTP ${chat.status} ${await chat.text()}`);
  let conversationId = (await chat.json())?.data?.conversation_id;
  if (!conversationId) {
    const list = await (await fetch(`${API}/conversations`, { headers })).json();
    conversationId = list?.data?.items?.[0]?.conversation_id;
  }
  if (!conversationId) fail("无法创建教师会话，不能生成方案候选。");
  const proposal = await fetch(`${API}/conversations/${conversationId}/proposals`, {
    method: "POST",
    headers,
    body: JSON.stringify({ student_id: "D16", node_id: run.node_ids[0], intent: "按当前学情重新生成一版安排" }),
  });
  if (proposal.status !== 202) fail(`生成方案候选失败：HTTP ${proposal.status} ${await proposal.text()}`);
  console.log("seed candidate ok");
}

await preflight();
await seedCandidate();
await mkdir(OUT, { recursive: true });

let browser;
try {
  browser = await chromium.launch({
    executablePath: process.env.BROWSER_EXECUTABLE,
    channel: process.env.BROWSER_EXECUTABLE ? undefined : "msedge",
    headless: true,
  });
} catch (error) {
  fail(`无法启动 Edge/Chrome：${String(error)}`);
}

const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
page.setDefaultTimeout(30000);
const consoleErrors = [];
page.on("console", (message) => {
  if (message.type() !== "error") return;
  const text = message.text();
  if (text.includes("403") || text.includes("404")) return;
  consoleErrors.push(text);
});

const shot = async (name, fullPage = true) => {
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage });
  console.log("ok", name);
};

const noOverflow = async (label) => {
  const size = await page.evaluate(() => ({ width: window.innerWidth, scroll: document.documentElement.scrollWidth }));
  assert.ok(size.scroll <= size.width + 1, `${label} 横向溢出：scrollWidth=${size.scroll} viewport=${size.width}`);
  console.log("ok 无横向溢出", label);
};

const nav = async (name) => {
  await page.getByRole("link", { name, exact: true }).first().click();
};

try {
  await page.goto(`${BASE}/identity`, { waitUntil: "networkidle" });
  await page.getByRole("heading", { name: "选择身份" }).waitFor();
  const datasetSelect = page.getByLabel("数据集");
  const options = await datasetSelect.locator("option").evaluateAll((nodes) =>
    nodes.map((node) => ({ value: node.value, text: node.textContent ?? "" })),
  );
  const real = options.find((option) => option.text.includes("40 名学生"));
  if (!real) fail("身份入口没有 40 名学生的真实数据集选项。");
  await datasetSelect.selectOption(real.value);
  await page.getByRole("button", { name: /^教师/ }).first().click();
  await page.getByRole("button", { name: /以教师身份进入/ }).click();
  await page.waitForURL(/\/teacher\/classes/);
  await page.getByText("名册人数", { exact: true }).waitFor();

  // 导航只能四项
  const teacherLinks = await page.getByRole("navigation", { name: "教师导航" }).getByRole("link").allInnerTexts();
  assert.deepEqual(
    teacherLinks.map((text) => text.trim()),
    ["班级总览", "课程体系", "数据管理", "AI 对话"],
    `教师导航应只有四项，实际：${teacherLinks.join("/")}`,
  );
  await shot("72-nav-four-1440");

  // 名册翻页
  await page.locator(".pageHeader select").first().selectOption("DC01");
  await page.getByText(/名册共 21 人/).waitFor();
  await page.getByRole("button", { name: "下一页" }).click();
  await page.getByText(/第 2 \/ 2 页/).waitFor();
  await page.locator("tr").filter({ hasText: "D21" }).waitFor();
  await page.getByText("名册共 21 人").waitFor();
  await shot("78-roster-page-2-1440");
  await page.getByRole("button", { name: "上一页" }).click();
  await page.getByText(/第 1 \/ 2 页/).waitFor();

  // D16 详情（唯一有正式画像的 40 人数据集学生）
  await page.locator("tr").filter({ hasText: "D16" }).getByRole("button", { name: "查看画像" }).click();
  await page.waitForURL(/\/teacher\/students\/D16/);
  await page.getByText("诊断发现", { exact: true }).waitFor();

  // 指标名标签
  await page.getByRole("button", { name: /查看依据 · 得分率|查看依据 · 满分题比例|查看依据 · 有作答比例|查看依据 · 题库覆盖率/ }).first().waitFor();
  await shot("76-evidence-metric-labels-1440");

  // 作答原文
  await page.getByRole("tab", { name: "作答记录" }).click();
  await page.getByRole("heading", { name: "作答记录" }).waitFor();
  await page.getByRole("button", { name: "查看依据" }).first().click();
  const drawer = page.getByRole("dialog", { name: "证据详情" });
  await drawer.waitFor();
  await drawer.getByText("实际作答").waitFor();
  const drawerText = await drawer.innerText();
  assert.ok(!drawerText.includes("未填写最终答案"), "作答依据显示“未填写最终答案”，需要后端确认原文读取");
  await shot("77-answer-original-1440", false);
  await page.keyboard.press("Escape");
  await drawer.waitFor({ state: "hidden" });

  // 方案区域与待审核候选（候选由 seedCandidate 通过真实接口生成）
  await page.getByRole("tab", { name: "学习方案" }).click();
  await page.getByText("任务组 1").first().waitFor();
  await page.getByRole("button", { name: /待审核候选（[1-9]/ }).waitFor();
  await shot("73-profile-plan-review-entry-1440");

  await page.getByRole("button", { name: /待审核候选（[1-9]/ }).click();
  await page.getByRole("dialog", { name: "方案审核" }).waitFor();
  const reviewDialog = page.getByRole("dialog", { name: "方案审核" });
  const firstCandidate = reviewDialog.locator("tbody tr").first();
  if (await firstCandidate.count()) await firstCandidate.click();
  await page.getByText("当前正式方案").waitFor();
  await page.getByText("候选变更摘要").waitFor();
  await shot("74-plan-review-detail-1440", false);
  await page.getByRole("button", { name: "通过", exact: true }).click();
  await page.getByRole("alertdialog", { name: "确认通过并发布候选方案" }).waitFor();
  await page.getByLabel("审核理由（可选）").fill("与当前学情一致，可执行");
  await page.getByRole("button", { name: "确认通过" }).click();
  await page.getByText("已通过并发布", { exact: true }).waitFor({ timeout: 30000 });
  await shot("75-plan-review-approved-1440", false);
  await page.getByRole("dialog", { name: "方案审核" }).getByRole("button", { name: "关闭", exact: true }).last().click();

  // 课程体系关系图
  await nav("课程体系");
  await page.getByRole("heading", { name: "课程体系" }).waitFor();
  await page.getByText("主题与指标", { exact: true }).waitFor();
  await page.getByText("暂无正式关系").waitFor();
  await noOverflow("课程体系 1440");
  await shot("79-curriculum-relations-1440");

  // 雷达 1366
  await page.setViewportSize({ width: 1366, height: 768 });
  await nav("班级总览");
  await page.getByText("名册人数", { exact: true }).waitFor();
  await page.locator(".pageHeader select").first().selectOption("DC01");
  await page.locator("tr").filter({ hasText: "D16" }).getByRole("button", { name: "查看画像" }).click();
  await page.waitForURL(/\/teacher\/students\/D16/);
  await page.getByText("观察指标").first().waitFor();
  await page.evaluate(() => window.scrollTo(0, 0));
  await noOverflow("学生画像 1366");
  await shot("80-radar-1366", false);
  await page.setViewportSize({ width: 1440, height: 900 });

  // 学生端：D01 的微课堂（真实 lesson）与 AI 对话（不需要 run_id）
  await page.getByRole("button", { name: /退出并重选身份/ }).first().click();
  await page.waitForURL(/\/identity/);
  await page.getByRole("heading", { name: "选择身份" }).waitFor();
  await page.getByRole("button", { name: /^学生/ }).first().click();
  const studentOptions = await page.getByLabel("数据集").locator("option").evaluateAll((nodes) =>
    nodes.map((node) => ({ value: node.value, text: node.textContent ?? "" })),
  );
  const studentReal = studentOptions.find((option) => option.text.includes("40 名学生"));
  if (!studentReal) fail("学生身份入口没有真实 40 人数据集。");
  await page.getByLabel("数据集").selectOption(studentReal.value);
  await page.getByRole("button", { name: "D01", exact: true }).click();
  await page.getByRole("button", { name: /以学生 D01 身份进入/ }).click();
  await page.waitForURL(/\/student\/profile/);
  const studentLinks = await page.getByRole("navigation", { name: "学生导航" }).getByRole("link").allInnerTexts();
  assert.deepEqual(studentLinks.map((text) => text.trim()), ["个人画像", "学习计划", "AI 对话"]);

  await nav("学习计划");
  await page.getByRole("heading", { name: "学习计划" }).waitFor();
  const lessonRow = page.locator(".activityRow").filter({ hasText: "微课堂" }).first();
  await lessonRow.getByRole("button", { name: /开始|继续/ }).click();
  await page.getByText("引用", { exact: true }).waitFor();
  await shot("82-micro-lesson-real-1440");
  await page.getByRole("button", { name: "返回学习计划" }).click();

  await nav("AI 对话");
  await page.getByText("会话历史", { exact: true }).waitFor();
  await page.locator("#chat-input").fill("我这次先练什么？");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await page.locator(".chatMsg").nth(1).waitFor({ timeout: 90000 });
  await shot("81-student-chat-real-1440");

  await browser.close();
} catch (error) {
  try {
    await page.screenshot({ path: path.join(OUT, "_failure.png"), fullPage: true });
  } catch {
    // ignore
  }
  await browser.close();
  fail(error instanceof Error ? `${error.message}\n${error.stack ?? ""}` : String(error));
}

if (consoleErrors.length) {
  console.error("\n浏览器控制台错误：");
  for (const line of consoleErrors) console.error(" -", line);
  fail("页面存在控制台错误。");
}
console.log(`\n真实数据页面自检通过，截图目录：${OUT}`);
