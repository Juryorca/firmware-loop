/**
 * continue-guard — "别自己停"的 harness 层护栏(pi agent_before_settle)
 *
 * 背景:循环规则写了「能自造就继续 / 成本类不算停止理由 / 降级需判定性理由且同面≥2轮」,
 * 但这些只写在提示词里 ⇒ 模型仍可能"自己收尾"(实测 2026-09-26:它把 H-13 以"成本高"降级、
 * 顺手宣布收尾,闸门因为"降级≠未处理"而放行)。
 * 本扩展在 **settle 之前**查一次工作区状态(只读探针 tools/queue_state.sh),**只在三种真问题上出声**
 *   (2026-09-29 用户反馈:每轮都提醒 = 噪声 ✗):
 *   ① **假收尾** —— 写了「队列清空/收尾/判据满足」但池里仍有未处理项;
 *   ② **降级理由不合规** —— 拿"成本/时间/工作量大"当理由降级;
 *   ③ **借口句式/资源声称** —— "需要真机"、"上下文余量不足"(按**实时占用**判;≥85% 才算)、"需工作区外"(要核对)。
 *   其余情况(本轮没写停止声明 = 正常结束一轮)⇒ **静默放行**;提醒带 **10 分钟冷却**;消息里附
 *   **实时上下文占用**(ctx.getContextUsage())+ "至少叠一格机械化"提醒。
 *
 * 设计约束(防乱搞):
 *   - 只在"队列非空 + 下一步指向 H"时触发;队列空(收尾态)⇒ 放行;
 *   - **每次 settle 最多提醒一次**,且必须先有工具活动才重置 ⇒ 不会来回刷屏/死循环;
 *   - 任何异常都吞掉(不能因为护栏本身把会话搞崩);
 *   - 每次提醒写一行 `.pi/continue-guard.log`(可观测)。
 */

import { execFileSync } from "node:child_process";
import { appendFileSync, existsSync, readFileSync, writeFileSync, statSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

// ROOT 解析:个人转发层注入的 FW_LOOP_ROOT 优先(pi 进程 cwd 可能不在本工作区)⇒ PI_PROJECT_DIR ⇒ cwd
const ROOT = process.env.FW_LOOP_ROOT || process.env.PI_PROJECT_DIR || process.cwd();
const LOG = `${ROOT}/.pi/continue-guard.log`;
const IS_FW_LOOP = existsSync(`${ROOT}/tools/queue_state.sh`);
let CTX_FILE = "";   // pi 在当前进程里没导出 PI_SESSION_FILE ⇒ 从 ctx.sessionManager 拿
let CTX_SID = "";   // 只在 fw-loop 工作区里生效(可被个人目录全局加载)
// 模块被加载就留痕 ⇒ 以后能区分"没加载"与"加载了但放行"
try { if (IS_FW_LOOP) appendFileSync(LOG, `${new Date().toISOString()} loaded (pid=${process.pid}, root=${ROOT})\n`); } catch {}

/** 每次 settle 都留一行(可观测:区分"护栏没跑"与"跑了但放行");超过 200KB 轮转一次 */
export function logLine(msg: string) {
  try {
    if (existsSync(LOG)) {
      const st = statSyncSafe(LOG);
      if (st > 200_000) writeFileSync(LOG, "");
    }
    appendFileSync(LOG, `${new Date().toISOString()} ${msg}\n`);
  } catch { /* 日志失败不影响主流程 */ }
}
function statSyncSafe(p: string): number { try { return require0(p); } catch { return 0; } }
function require0(p: string): number { try { return Number(statSync(p).size); } catch { return 0; } }


/** 是否启用护栏:只认**本会话的类型**(第一条 user 消息是不是 /fw 研究提示词)。
 *  工作区里同时有框架开发/复核会话 ⇒ 不能拿"工作区里有某个文件"当判据。 */
const FW_PROMPT_MARK = "你是固件漏洞研究主循环 agent";

function firstUserText(file: string): string {
  try {
    const lines = readFileSync(file, "utf8").split("\n").slice(0, 40);
    for (const line of lines) {
      if (!line.trim()) continue;
      let rec: any;
      try { rec = JSON.parse(line); } catch { continue; }
      const m = rec?.message ?? rec;
      if (m?.role !== "user") continue;
      const c = m.content;
      if (typeof c === "string") return c;
      if (Array.isArray(c)) return c.map((x: any) => x?.text || "").join("");
      return "";
    }
  } catch { /* 读不到就当非研究会话 */ }
  return "";
}

export function guardActive(): boolean {
  try {
    if (!IS_FW_LOOP) return false;
    const f = CTX_FILE || process.env.PI_SESSION_FILE || "";
    if (!f) return false;
    return firstUserText(f).includes(FW_PROMPT_MARK);
  } catch { return false; }
}


/* ───────── 判定(纯函数,可单测):只认"借口句式",不做裸词匹配 ───────── */
export type Decision = { action: "pass" | "nudge"; reason: string; content?: string };

// 借口句式(必须成句,孤立的"硬件/成本/仿真器"不算)
export const EXCUSE = /(?:需要|要|得|须|只能靠|必须)[^。；]{0,8}(?:真机|物理机|实机|物理设备)|(?:真机|物理机|实机)[^。；]{0,4}(?:才能|才可以|才可|无法替代)|工作量大|工作量太大|(?:成本|代价)[^。；]{0,4}(?:高|太大|过大)|(?:预算|时间|步数|上下文)[^。；]{0,4}(?:不够|不足|太紧|有限|余量)|仿真器[^。；]{0,10}(?:缺口|限制|不支持|能力不足|缺失)|(?:缺|没有|拿不到)[^。；]{0,6}前置状态|前置状态[^。；]{0,8}(?:造不出|没造出|无法造)/;
// "结论类"合法停(有 finding 必须写明是本轮新成立;判据满足=收尾达标)
export const CONCLUSION = /本轮[^。；]{0,10}(?:新|成立)[^。；]{0,6}finding|本轮[^。；]{0,10}finding[^。；]{0,6}(?:新|成立)|判据满足/;
export const FINDING_CLAIM = /有 ?finding/;
export const HISTORY = /既有|以往|此前|历史|之前[^。；]{0,4}finding/;
export const RESOURCE_CLAIM = /需工作区外|工作区之外|外部限制|工具[^。；]{0,4}不可用|环境[^。；]{0,4}不可用|BLOCKER/;
export const USER_ESCAPE = /用户[^。；]{0,6}(?:要求|指示|决定|说|叫|喊)|暂停|可选[^。；]{0,4}需用户裁决/;

/** 提醒里统一附:实时上下文占用 + "换格不是停"提醒(2026-09-29 用户要求:把真实占用报给它看) */
export function ctxLine(ctxPct: number | null): string {
  const p = ctxPct === null ? "占用未知(刚压缩?)" : `实时占用 ${ctxPct.toFixed(0)}%`;
  const hint = ctxPct !== null && ctxPct < 85 ? " ⇒ '上下文余量'**不成立**,继续做" : (ctxPct !== null ? " ⇒ 确实偏高:可先 /compact 再继续" : "");
  return `\n[上下文] ${p}${hint}\n[方法] 本轮若只有静态/读码 ⇒ 至少叠**一格机械化**(fuzz/符号执行/插桩/等价 oracle;换格不是停,见 LOOP「机械化 oracle 的选择」)`;
}

export function decide(stop: string, open: number, bad: number, next: string, ctxPct: number | null = null, roundState: string | null = null, userSaidClose = true): Decision {
  // ① 用户级:用户要求暂停 / 明确写成"可选、需用户裁决" ⇒ 放行
  if (USER_ESCAPE.test(stop)) return { action: "pass", reason: "user-pause" };
  // ①b **收尾必须用户确认**(2026-10-02):声称"队列清空/判据满足/收尾/coverage complete"但用户从未说过收尾/归档 ⇒ 提醒
  if (!userSaidClose && /队列清空|判据满足|收尾|coverage complete|全部判完/.test(stop)) {
    return { action: "nudge", reason: "closure-needs-user", content:
      `【继续护栏·收尾要用户确认】**你自己宣布收尾了,但用户没说过"收尾"** ✗\n` +
      `规则(AGENTS Stop Rules / LOOP 两档表):判据满足或队列清空 ⇒ **停下汇报 + 备好 closure 材料,等用户一句话确认**(参考"收尾了"/"归档了")⇒ 才能写 CLOSURE / 执行清理。\n` +
      `先把材料备齐(INDEX / attack-surface / CLOSURE 草稿 / 覆盖度摘要在 notes 里),然后**汇报等确认**。` + ctxLine(ctxPct) };
  }
  // ② 队列真空 ⇒ 收尾合法(不管理由里写了什么词)
  if (open <= 0 && bad <= 0) return { action: "pass", reason: "queue-empty" };
  // ③ 结论类合法停 ⇒ 放行(前提:有 finding 必须写"本轮";**且"收尾类"声称必须与队列一致**)
  const claimsDone = /队列清空|收尾|全部判完|已收尾|判据满足/.test(stop);
  if (CONCLUSION.test(stop) && !(FINDING_CLAIM.test(stop) && HISTORY.test(stop))) {
    if (claimsDone && open > 0) {
      return { action: "nudge", reason: "false-closure", content:
        `【继续护栏】**假收尾**:你写了「${stop.slice(0, 60)}」,但假设池里仍有 **${open} 条未处理** ✗\n` +
        `按 LOOP「自动继续」:队列非空就不能以"判据满足/队列清空/收尾"为由停 —— 要么继续取队首,要么把那些条目逐条给结论或**显式降级**(降级理由须是判定类)。\n` +
        `STOP: ${stop.slice(0, 120)}` + ctxLine(ctxPct) };
    }
    return { action: "pass", reason: "conclusion" };
  }
  // ④ 拿历史 finding 当理由 ⇒ 提醒(这是最常见误用)
  if (FINDING_CLAIM.test(stop) && HISTORY.test(stop)) {
    return { action: "nudge", reason: "claim-history", content:
      `【继续护栏】停止理由引的是**以往**的 finding。\n按 AGENTS「停 / 不停」①:"有 finding"只指**本轮新成立**的(本轮把某个未判定面推到 Vulnerable,或本轮新增/加强结论);历史 finding(已备料/已进 report/)不算。\n⇒ 队列还有 ${open} 条未处理:请**本轮内接着取队首做下去**。\nSTOP: ${stop.slice(0,120)}` + ctxLine(ctxPct) };
  }
  // ④b 上下文类理由 ⇒ 按**实时占用**判(护栏会给出真实数字;≥85% 才算理由)
  if (/上下文|context/i.test(stop)) {
    if (ctxPct !== null && ctxPct >= 85) return { action: "pass", reason: "context-high" };
  }
  // ⑤ 借口句式 ⇒ 提醒(反例清单)
  const ex = stop.match(EXCUSE);
  if (ex) {
    return { action: "nudge", reason: "excuse-pattern", content:
      `【继续护栏·反例】你的停止理由用了借口句式:「${ex[0]}」。\nAGENTS「不算停的常见伪装」:真机/硬件(本工作区没有,仿真即准绳)、工作量大/成本/时间/预算、仿真器缺某能力、前置状态没造出 —— **都不是**合法停的理由。\n合法停只有四种:①**本轮新成立** finding ②需工作区外**具体外部事物**(账号·样本·凭据·宿主环境,不含真机)③队列清空 ④目标级裁决。\n⇒ 要么继续做(先试技术已在 notes),要么该行写成"可选、需用户裁决"。队列还有 ${open} 条。\nSTOP: ${stop.slice(0,120)}` + ctxLine(ctxPct) };
  }
  // ⑥ 声称需要外部资源 ⇒ 核对提醒(不盲信)
  if (RESOURCE_CLAIM.test(stop)) {
    return { action: "nudge", reason: "verify-claim", content:
      `【继续护栏·核对】你声称的停止理由是资源类:\n${stop.slice(0,140)}\n先按 AGENTS「不算停的常见伪装」自查(真机?工作量大?成本?都不是合法理由);若确需工作区外**具体外部事物**,写清四要素:要什么 / 挡住哪一步 / 为何不能自造 / 给了能解锁什么。\n否则继续做。队列还有 ${open} 条。` + ctxLine(ctxPct) };
  }
  // ⑦ 只在**两种真问题**上出声(2026-09-29 用户反馈:每轮都提醒 = 噪声 ✗):
  //    ① 它**声称收尾/队列清空**,但池里还有未处理项 ⇒ 说假话;
  //    ② 存在**降级理由不合规**的条目(成本类当理由)✓
  //    其余情况(本轮没写停止声明 = 只是正常结束一轮)⇒ **静默放行**,不打扰。
  // ②b **半轮**:本轮声明了目标 H 但没把它推到结论 ⇒ 不是合法的"轮",不算停
  if (roundState === "half" && open > 0) {
    return { action: "nudge", reason: "half-round", content:
      `【继续护栏·半轮】**你声明了本轮的 H,但没把它推到结论** ⇒ 这不算"一轮" ✗\n` +
      `定义(LOOP):**一轮 = 把一条假设推进到 oracle 结果或判定性结论**(该 H 状态要变成 定论/降级,并带证据)。\n` +
      `把这条 H 做完(或按"降级准入"给出判定性理由降级)再收本轮;队列还有 ${open} 条。` + ctxLine(ctxPct) };
  }
  // ③ 队列非空 ⇒ 拉回(轮末也拉;消息压短 + reason 分清三种来源)
  if (bad > 0 || (claimsDone && open > 0) || open > 0) {
    const head = bad > 0
      ? `【继续护栏】池里有 ${bad} 条**降级理由不合规**(成本类不算理由)✗`
      : claimsDone
        ? `【继续护栏】**假收尾**:你说收尾/队列清空,池里仍有 ${open} 条未处理 ✗`
        : `【继续护栏】队列仍有 ${open} 条未处理 ⇒ **本轮内继续取队首**(默认自动继续)`;
    const reason = bad > 0 ? "bad-downgrade" : claimsDone ? "false-closure" : "queue-open";
    return { action: "nudge", reason, content:
      head + "\n" +
      `按 LOOP「自动继续 / 降级准入」:能自造就继续做(从池中未处理项取队首;先试技术通常已在 notes 里)。\n` +
      `"成本高 / 时间不够 / 要建复杂前置 / 需要真机"都不是停止或降级的理由;\n` +
      `合法停只有四种:①本轮新成立 finding ②需工作区外具体外部事物 ③队列清空 ④目标级裁决;要交给用户裁决就写成"可选、需用户裁决"。\n` +
      (next ? `(notes 里可参考的下一步行: ${next.slice(0, 120)})` : `(notes 未给下一步行 ⇒ 直接取池中未处理项)`) +
      ctxLine(ctxPct) };
  }
  return { action: "pass", reason: "nothing-open" };
}

export default function (pi: ExtensionAPI) {
  let nudgedThisSettle = false;
  let lastNudgeAt = 0;                    // 冷却(防刷屏):默认 90 秒 —— 10 分钟太长会把"轮末拉回"也吃掉 ✗(2026-09-29 实测)

  // 有工具活动 ⇒ 新一轮工作开始,允许下次 settle 再提醒一次
  pi.on("tool_call", () => {
    nudgedThisSettle = false;
  });

  // 事件探针(诊断用):确认 pi 在这个启动方式下派发哪些事件
  for (const ev of ["agent_settled", "turn_end", "session_shutdown"]) {
    try { pi.on(ev as any, () => { logLine(`evt:${ev}`); }); } catch {}
  }

  // 开场标识(用户要求):会话一开始就说清"护栏在不在、管不管这个窗口、队列/上下文如何"
  pi.on("session_start", (_e: any, ctx: any) => {
    try {
      logLine("evt:session_start");
      try {
        const sm = ctx?.sessionManager;
        CTX_FILE = (typeof sm?.getSessionFile === "function" ? sm.getSessionFile() : "") || CTX_FILE || "";
        CTX_SID  = (typeof sm?.getSessionId  === "function" ? sm.getSessionId()  : "") || CTX_SID  || "";
      } catch {}
      const on = guardActive();
      let open = 0, bad = 0;
      if (on) {
        try {
          const out = execFileSync("bash", [`${ROOT}/tools/queue_state.sh`], { cwd: ROOT, encoding: "utf8", timeout: 20000 });
          open = Number((out.match(/OPEN=(\d+)/) || [])[1] || 0);
          bad  = Number((out.match(/BAD=(\d+)/) || [])[1] || 0);
        } catch {}
      }
      let pct: number | null = null;
      try { const cu = ctx?.getContextUsage?.(); if (cu && typeof cu.percent === "number") pct = cu.percent; } catch {}
        const body = `【护栏】已加载 ✓ · 窗口=研究会话(会管) · 队列未处理 ${open} 条`
                     + `${bad ? ` / 降级不合规 ${bad} 条` : ""}`
                     + (pct !== null ? ` · 上下文 ${pct.toFixed(1)}%` : "")
                     + `\n  · 我**只在三种情况出声**:①说"收尾/队列清空"但队列非空 ②降级理由不合规 ③借口句式`
                     + `\n  · 提醒带 10 分钟冷却;明细见 .pi/continue-guard.log`;
      logLine(`session_start notice on=${on} OPEN=${open}`);
      try { ctx?.ui?.notify?.(body.replace(/\n/g, "  "), "info"); } catch {}
      try { pi.sendMessage({ customType: "continue-guard", content: body, display: true }, { triggerTurn: false }); } catch {}
      return;
    } catch (e: any) { try { logLine("session_start ERROR " + String(e?.message || e).slice(0, 100)); } catch {} }
  });

  pi.on("agent_before_settle", (event: any, ctx: any) => {
    try {
      try {
        const sm = ctx?.sessionManager;
        CTX_FILE = (typeof sm?.getSessionFile === "function" ? sm.getSessionFile() : "") || CTX_FILE || "";
        CTX_SID  = (typeof sm?.getSessionId  === "function" ? sm.getSessionId()  : "") || CTX_SID  || "";
      } catch {}
      logLine(`evt:agent_before_settle continue=${!!event?.continue} file=${CTX_FILE ? "yes" : "no"}`);
      if (!guardActive()) { logLine(`settle skip(guardActive=false root=${ROOT} isFwLoop=${IS_FW_LOOP})`); return; }                  // 本窗口被关/非研究窗口 ⇒ 不介入
      if (event?.continue) { logLine(`settle skip(pi 本就要继续)`); return; }   // 本来就要继续
      if (nudgedThisSettle) { logLine(`settle skip(本轮已提醒过一次)`); return; }

      logLine(`settle check(probe 前)`);
      const out = execFileSync("bash", [`${ROOT}/tools/queue_state.sh`], {
        cwd: ROOT, encoding: "utf8", timeout: 20_000,
      }).trim();
      const open = Number((out.match(/OPEN=(\d+)/) || [])[1] || 0);
      const bad = Number((out.match(/BAD=(\d+)/) || [])[1] || 0);
      const stop = (out.match(/STOP=(.*?) NEXT=/) || [])[1] || "";
      const next = (out.match(/NEXT=(.*)/) || [])[1] || "";
      // 实时上下文占用(pi 的 ctx;用户要求把真实数字报给模型看)
      let cu: any = null;
      try { cu = ctx?.getContextUsage?.() || null; } catch {}
      const ctxPct = cu && typeof cu.percent === "number" ? cu.percent : null;
      let userSaidClose = true;      // 会话尾部有没有用户说过"收尾/归档/不要了"
      try {
        if (CTX_FILE) {
          const tail = readFileSync(CTX_FILE, "utf8").slice(-200_000);
          let said = false;
          for (const line of tail.split("\n")) {
            if (!line.includes('"role":"user"')) continue;
            let d: any; try { d = JSON.parse(line); } catch { continue; }
            const m = d?.message ?? d; if (m?.role !== "user") continue;
            const c = m?.content;
            const t = typeof c === "string" ? c : Array.isArray(c) ? c.map((x: any) => x?.text ?? "").join("") : "";
            if (/收尾|归档|不要了/.test(t)) { said = true; break; }
          }
          userSaidClose = said;
        }
      } catch {}
      let roundState: string | null = null;
      try { roundState = (out.match(/ROUND=([a-z?]+)/) || [])[1] || null; } catch {}
      const d = decide(stop, open, bad, next, ctxPct, roundState, userSaidClose);
      logLine(`settle ${d.action.toUpperCase()}(${d.reason}) OPEN=${open} ctx=${ctxPct === null ? "?" : ctxPct.toFixed(0) + "%"} STOP=${stop.slice(0, 80)}`);
      if (d.action === "pass") return;
      const COOLDOWN_MS = Number(process.env.PI_GUARD_COOLDOWN_MS || 90_000);   // 可用环境变量覆盖
      if (Date.now() - lastNudgeAt < COOLDOWN_MS) { logLine(`settle SKIP(cooldown ${(COOLDOWN_MS/1000)|0}s) ${d.reason}`); return; }
      lastNudgeAt = Date.now();
      nudgedThisSettle = true;
      return { continue: true, entries: [{ type: "custom_message", customType: "continue-guard", display: true, content: d.content! }] };
    } catch (e: any) {
      try { logLine(`settle ERROR ${String(e?.message || e).slice(0,120)}`); } catch {}
      return; // 护栏自身出错 ⇒ 放行(但留证)
    }
  });
}
