/**
 * 页面自检：用本机 Edge 打开开发服务器，按真实点击路径逐页截图并收集控制台错误。
 * 用法：node scripts/verify-pages.mjs [输出目录]（需先 npm run dev）
 *
 * 注意：会话只存在内存里，所以脚本不直接 goto 子路径，全部通过页面内点击跳转，
 * 这同时也验证了导航本身是否可用。
 */
import { chromium } from "playwright-core";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import assert from "node:assert/strict";

const BASE = process.env.BASE_URL ?? "http://127.0.0.1:5173";
const OUT = process.argv[2];

if (OUT) await mkdir(OUT, { recursive: true });

const executablePath = process.env.BROWSER_EXECUTABLE;
const browser = await chromium.launch({
  ...(executablePath ? { executablePath } : { channel: "msedge" }),
  headless: true,
});
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.setDefaultTimeout(10000);
if (process.env.USE_BUILT_FILES === "1") {
  await page.route(BASE + "/**", (route) => {
    const pathname = new URL(route.request().url()).pathname;
    return route.fulfill({ path: pathname.startsWith("/assets/") ? `dist${pathname}` : "dist/index.html" });
  });
}

const errors = [];
page.on("console", (msg) => {
  if (msg.type() === "error") errors.push(msg.text());
});
page.on("pageerror", (err) => errors.push(String(err)));

const shot = async (name) => {
  if (OUT) await page.screenshot({ path: join(OUT, `${name}.png`), fullPage: true });
  console.log("ok", name);
};

await page.goto(BASE + "/", { waitUntil: "networkidle" });
await page.getByText("智慧学伴", { exact: false }).first().waitFor();
await shot("01-identity");

// 教师端
await page.getByRole("button", { name: /以教师身份进入/ }).click();
await page.waitForURL(/\/teacher\/classes/);
await page.getByText("班级学情摘要").first().waitFor();
await shot("02-teacher-classes");

await page.getByRole("button", { name: "班级对比" }).click();
await page.getByText("班级对比").first().waitFor();
await shot("03-teacher-compare");
await page.getByRole("button", { name: "返回班级" }).click();
await page.getByRole("button", { name: "查看详情", exact: true }).click();
await page.getByText("课程观察指标").waitFor();
await page.getByText("班级问题与依据").waitFor();
await page.getByText("6.7 / 10", { exact: true }).waitFor();
await shot("03a-class-detail");
await page.getByRole("button", { name: "返回班级" }).click();

await page.getByRole("button", { name: /查看画像/ }).first().click();
await page.waitForURL(/\/teacher\/students\//);
await page.getByText("诊断发现").first().waitFor();
await page.getByText("3.3 / 10", { exact: true }).waitFor();
await shot("04-teacher-profile");
await page.getByRole("button", { name: "作答记录", exact: true }).click();
await page.getByText("关联作答记录").waitFor();
await page.getByRole("button", { name: "学情概览", exact: true }).click();

await page.getByRole("button", { name: /查看依据 · 作答记录/ }).first().click();
await page.getByRole("dialog").waitFor();
await page.getByRole("dialog").getByText("原题", { exact: true }).waitFor();
await page.getByRole("dialog").getByText("解方程：3x − 5 = x + 7。").waitFor();
await shot("05-evidence-panel");
await page.keyboard.press("Escape");
await page.getByRole("dialog").waitFor({ state: "hidden" });
assert.equal(await page.evaluate(() => document.activeElement?.textContent?.includes("作答记录")), true);

await page.getByRole("button", { name: "调整学习建议" }).click();
await page.getByRole("button", { name: "保存示例版本" }).click();
await page.getByRole("alert").getByText("请填写学习目标和修改理由。").waitFor();
await page.getByLabel("修改理由").fill("先观察验算步骤");
await page.getByRole("button", { name: "保存示例版本" }).click();
await page.getByText("当前示例版本 2").waitFor();
await page.getByText("查看原版本 1").click();
await page.getByText("改为下一版本的理由：先观察验算步骤").waitFor();

await page.getByRole("link", { name: "返回班级" }).click();
await page.getByRole("button", { name: "乙班", exact: true }).click();
await page.getByText("DEMO-S3", { exact: true }).waitFor();
await page.getByRole("button", { name: /查看画像/ }).first().click();
await page.getByRole("link", { name: "就此学生提问" }).click();
await page.waitForFunction(() => document.querySelector('select option[value="student:DEMO-S3"]')?.selected);
await page.getByRole("link", { name: "查看该对象的学情" }).click();
await page.getByRole("link", { name: "返回班级" }).click();
await page.getByRole("button", { name: "乙班", exact: true, pressed: true }).waitFor();

if (!(await page.getByLabel("数据集").isVisible())) {
  await page.getByText("数据范围", { exact: true }).click();
}
await page.getByLabel("数据集").selectOption("DEMO-DATA-OBSERVED");
await page.getByText("DEMO-RUN-OBSERVED（已完成）", { exact: true }).waitFor({ state: "attached" });
await page.getByText("DEMO-S3", { exact: true }).waitFor();
await page.getByRole("button", { name: /查看画像/ }).first().click();
await page.getByText("补写代入检验", { exact: true }).waitFor();
await page.getByText("6.7 / 10", { exact: true }).waitFor();
if (!(await page.getByLabel("数据集").isVisible())) {
  await page.getByText("数据范围", { exact: true }).click();
}
await page.getByLabel("数据集").selectOption("DEMO-DATA");
await page.getByText("先补一次观察，再决定练什么", { exact: true }).waitFor();
assert.equal(await page.getByText("补写代入检验", { exact: true }).count(), 0);

await page.getByRole("link", { name: "课程体系", exact: true }).click();
await page.getByText("观察指标").first().waitFor();
await shot("06-teacher-curriculum");

await page.getByRole("link", { name: "数据管理", exact: true }).click();
await page.getByRole("button", { name: "检查示例选件" }).click();
await page.getByText("请补选：", { exact: false }).waitFor();
const files = page.locator('input[type="file"]');
assert.equal(await files.count(), 6);
for (let index = 0; index < 6; index += 1) {
  await files.nth(index).setInputFiles({ name: `sample-${index}.csv`, mimeType: "text/csv", buffer: Buffer.from("example\n") });
}
await page.getByRole("button", { name: "检查示例选件" }).click();
await page.getByText("六个文件位已选齐。", { exact: false }).waitFor();
await page.getByRole("button", { name: "分析运行", exact: true }).click();
await page.getByRole("button", { name: "模拟一次失败" }).click();
await page.getByRole("alert").getByText("模拟任务失败，可重新运行。").waitFor();
await page.getByRole("button", { name: "模拟一次运行" }).click();
await page.getByText("刚才的模拟任务已结束", { exact: false }).waitFor();
await shot("07-teacher-data");

await page.getByRole("link", { name: "教学对话", exact: true }).click();
await page.getByText("模型服务尚未配置").first().waitFor();
await shot("08-teacher-chat");

await page.setViewportSize({ width: 360, height: 800 });
for (const name of ["课程体系", "教学对话"]) {
  await page.getByRole("link", { name, exact: true }).click();
  await page.getByText(name === "课程体系" ? "观察指标" : "模型服务尚未配置", { exact: true }).first().waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, name);
  await shot(name === "课程体系" ? "13-mobile-curriculum" : "14-mobile-chat");
}
await page.setViewportSize({ width: 1440, height: 900 });

// 回到身份选择，换学生
await page.getByRole("button", { name: /退出并重选身份/ }).click();
await page.getByRole("button", { name: /以教师身份进入/ }).waitFor();

await page.getByRole("button", { name: /DEMO-S1/ }).click();
await page.waitForURL(/\/student\/home/);
await page.getByText("我的学习表现").first().waitFor();
await shot("09-student-home");

await page.getByRole("link", { name: "学习计划", exact: true }).click();
await page.getByText("我的学习计划").first().waitFor();
await shot("10-student-plan");

// 逐步提示
await page.getByRole("button", { name: "查看第一条提示" }).first().click();
await page.getByText("提示 1：", { exact: false }).first().waitFor();
assert.equal(await page.getByText("提示 1：", { exact: false }).count(), 1);
await shot("11-student-hint");
await page.getByRole("button", { name: "打开材料 DEMO-M2" }).click();
await page.getByRole("dialog").getByText("解方程：3x − 5 = x + 7。").waitFor();
assert.equal(await page.getByRole("dialog").getByText("参考解析", { exact: false }).count(), 0);
await page.keyboard.press("Escape");

await page.getByRole("link", { name: "学习对话", exact: true }).click();
await page.getByText("模型服务尚未配置").first().waitFor();
await shot("12-student-chat");

await browser.close();

if (errors.length > 0) {
  console.error("控制台错误：");
  for (const line of errors) console.error(" -", line);
  process.exitCode = 1;
} else {
  console.log("没有控制台错误");
}
