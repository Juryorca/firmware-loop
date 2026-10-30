/**
 * screenshot-guard — 截图证据链护栏(pi tool_call 拦截)
 *
 * 背景:规范要求「截图命令 = 直接运行提交包内的 PoC 脚本,禁止出现中间产物文件名」。
 * 光把 tools/termshot.sh 改成声明式签名**并不闭环** —— agent 仍可直接 bash 调
 * `tmux capture-pane`/`send-keys` 手做终端画面,或用 convert 造一张 png。
 * 本扩展在 tool_call 层把这些路堵住,让"随意使用指令"变成**不可能**。
 *
 * 放行面:唯一的正路是 `tools/termshot.sh <shots.json> <shot-id>`。
 * 未在此列出的截图方式一律 block;误伤可按 reason 里给的正确用法改。
 */

import { isToolCallEventType } from "@earendil-works/pi-coding-agent";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const USAGE =
  "正路: 先写 report/attachments/<描述性名称>/shots.json(每条含 id/why/script/argv)," +
  "再跑 tools/termshot.sh <shots.json> <shot-id> —— 它会校验脚本在包目录内、sha256 与 argv 白名单," +
  "并输出 <shot-id>.png 与 .provenance。不要用自由命令截图。";

/** 把 shell 命令粗略切成 token(够用即可;只做 arity/关键字判断) */
function tokens(cmd: string): string[] {
  return cmd.match(/(?:"[^"]*"|'[^']*'|\S+)/g) ?? [];
}

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", (event) => {
    if (isToolCallEventType("bash", event)) {
      const cmd = String(event.input.command ?? "");

      // 1) termshot 必须是 <manifest> <shot-id> 两参;旧的自由命令形式一律拒
      const tk = tokens(cmd);
      const i = tk.findIndex((t) => /(^|\/)termshot\.sh$/.test(t.replace(/["']/g, "")));
      if (i >= 0) {
        // 只数到下一个 shell 操作符为止(否则 `|`、`2>&1` 等会被误算成参数 -> 假阳)
        const rest = tk.slice(i + 1);
        const stop = rest.findIndex((t) => /^\d*[|&;<>]/.test(t));
        const args = (stop >= 0 ? rest.slice(0, stop) : rest).filter((t) => !t.startsWith("-"));
        if (args.length !== 2) {
          return { block: true, reason: `termshot.sh 只接受 <shots.json> <shot-id>(收到 ${args.length} 个位置参数)。${USAGE}` };
        }
      }

      // 2) 不许手搓 tmux 终端画面来伪造"真实终端截图"
      if (/\btmux\b/.test(cmd) && /\b(capture-pane|send-keys|new-session)\b/.test(cmd)) {
        return { block: true, reason: `禁止直接驱动 tmux 产出终端画面(等于手工重绘截图)。${USAGE}` };
      }

      // 3) 不许用转换器把中间件"造"成报告里的 png
      if (/\.png\b/.test(cmd) && /\breport\//.test(cmd) && /\b(convert|magick|import|ffmpeg|scrot|gnome-screenshot)\b/.test(cmd)) {
        return { block: true, reason: `禁止用图像转换/截屏工具生成 report/ 下的 png(截图必须源自真实终端运行)。${USAGE}` };
      }
      return;
    }

    // 4) write/read 工具直接写 png 也要拦(绕过 termshot 的二进制写入)
    if (isToolCallEventType("write", event)) {
      const p = String(event.input.path ?? "");
      if (/\.png$/i.test(p) && /(^|\/)report\//.test(p)) {
        return { block: true, reason: `禁止直接写 report/ 下的 png。${USAGE}` };
      }
      return;
    }

    return;
  });
}
