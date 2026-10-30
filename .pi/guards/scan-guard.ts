/**
 * scan-guard — 禁止"全盘扫描"的护栏(pi tool_call 拦截)
 *
 * 背景:实测有 session 用 `find / -name ...` / `find / -type d -iname 'AFLplusplus*'` 找工具 —— WSL 上可能耗时数分钟到数十分钟,
 * 还会翻到其它项目目录;而目标工具其实在 AGENTS.md 明写的路径里。规则见 `AGENTS.md` 守则 12b 与 `LOOP.md`「文件搜索 / 扫描边界」。
 *
 * 判定(见导出的 scanDecision,便于单测):
 *   - 命中"递归/遍历类工具"(find / rg / du / tree / grep -r|-R)且搜索根是 `/` 或 `/mnt/<盘>` ⇒ **block**;
 *   - `/mnt/*`(Windows 盘)一律 block(即使带许可标记);
 *   - 用户在对话里明确许可后,可带注释标记 `# user-approved-root-scan` **并且** 带 `-xdev` 再跑 ⇒ 放行;
 *   - 其它情况(搜索限定在已知路径)⇒ 放行。
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const MARKER = "user-approved-root-scan";

const REASON = [
  "禁止全盘扫描(AGENTS 守则 12b / LOOP「文件搜索 / 扫描边界」)。",
  "请改用低成本阶梯:command -v <tool> → dpkg -L <pkg> 2>/dev/null → ls /usr/bin/<tool>* /usr/local/bin/<tool>* → find <已知目录> -maxdepth 4 -iname '<pat>'。",
  "已知路径优先:$PWD、state/<目标>/、~/src、~/opt、/usr/bin、/usr/lib、/usr/local、/opt、/etc。",
  "确需从根搜(先问用户):带注释 '# user-approved-root-scan' **且** 用 `find / -xdev \\( -path /mnt -o -path /proc -o -path /sys -o -path /dev -o -path /run \\) -prune -o -iname '<pat>' -print`。",
  "Windows 盘 /mnt/c、/mnt/d 上的递归扫描一律禁止(WSL 9p/drvfs 极慢)。",
].join("\n");

export type Decision = { block: boolean; reason?: string; why: string };

/** 判定一条 bash 命令是否属于"禁止的全盘扫描"(纯函数,便于测试) */
export function scanDecision(cmd: string): Decision {
  const c = String(cmd ?? "");
  const marker = c.includes(MARKER);
  const xdev = /(^|\s)-xdev(\s|$)/.test(c);
  const win = /\/mnt\/[a-z](\/|\s|$)/.test(c);
  const tool = /(^|[|;&]\s*)(find|grep|egrep|rg|du|tree|ls)\b/.exec(c);
  if (!tool) return { block: false, why: "非遍历类工具" };
  const t = tool[2];
  const recursive = t === "find" || t === "rg" || t === "du" || t === "tree" || /(^|\s)-\w*[rR]/.test(c);
  if (!recursive) return { block: false, why: "非递归调用" };
  const rootSlash = /(^|\s)\/(\s|$|\*)/.test(c);
  if (win) return { block: true, reason: `Windows 挂载盘的递归扫描禁止。\n${REASON}`, why: "扫描 /mnt/<盘>" };
  if (!rootSlash) return { block: false, why: "搜索根不是 /" };
  if (marker && xdev) return { block: false, why: "用户已许可 + -xdev" };
  if (marker && !xdev) return { block: true, reason: `从根搜索必须加 -xdev。\n${REASON}`, why: "带许可但缺 -xdev" };
  return { block: true, reason: REASON, why: "从根全盘扫描" };
}

export default function (pi: ExtensionAPI) {
  try {
    pi.on("tool_call", (event: any) => {
      try {
        if (event?.type !== "tool_call" || event?.name !== "bash") return;
        const cmd = String(event?.input?.command ?? event?.arguments?.command ?? "");
        const d = scanDecision(cmd);
        if (d.block) return { block: true, reason: d.reason };
      } catch {
        return; // 护栏自身出错 ⇒ 放行
      }
    });
  } catch {
    /* 注册失败不影响会话 */
  }
}
