"""MCP 内容转换与安全边界。

把 MCP `content` / `resources/read` 的块转换成内部模型消息可用的
text/image 部件。图片和二进制只保存临时文件，模型历史里存路径与哈希，
不把 base64 塞进 JSON/数据库；模型适配层在真正请求时再读取并编码。

这里的安全上限是本地工程边界（不是 Pi 的 MCP 文本 20 KiB）：
- 单张图片解码后 4 MiB；
- 单个 MCP 结果最多 8 张图片。
超限/非法 base64/MIME 一律降级成文本说明，不让工具结果崩溃。
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import mimetypes
import os
import tempfile
from typing import Any

from .output import save_temp_text, truncate_middle

MCP_OUTPUT_MAX_BYTES = 20 * 1024
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_BINARY_BYTES = 8 * 1024 * 1024
MAX_IMAGES_PER_RESULT = 8

IMAGE_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

TEMP_IMAGE_PREFIX = "fwcore-mcp-image"
TEMP_RESOURCE_PREFIX = "fwcore-mcp-resource"


def _save_bytes(data: bytes, *, prefix: str, suffix: str,
                store_dir: str | None = None) -> str:
    """保存图片/二进制；有持久目录时用内容哈希，跨进程/checkpoint 可读。"""
    if store_dir:
        try:
            os.makedirs(store_dir, exist_ok=True)
        except OSError as e:
            raise OSError(f"无法创建 MCP 内容目录 {store_dir}: {e}") from e
        name = hashlib.sha256(data).hexdigest() + suffix
        path = os.path.join(store_dir, name)
        if os.path.isfile(path):
            return path
        fd, tmp = tempfile.mkstemp(prefix=".mcp-", suffix=suffix, dir=store_dir)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        return path
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(path)
        raise
    return path


def save_temp_bytes(data: bytes, *, prefix: str, suffix: str) -> str:
    return _save_bytes(data, prefix=prefix, suffix=suffix)


def _base_mime(mime: str | None) -> str:
    return (mime or "").split(";", 1)[0].strip().lower()


def _is_text_mime(mime: str | None) -> bool:
    base = _base_mime(mime)
    return (base.startswith("text/") or base == "application/json"
            or base.endswith(("+json", "+xml")))


def _decode_base64(data: Any) -> bytes | None:
    if not isinstance(data, str) or not data:
        return None
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None


def image_part(data_b64: Any, mime: Any, *, label: str = "image",
               store_dir: str | None = None) -> dict:
    """MCP image block -> 内部 image part；任何非法输入降级为 text。"""
    mime_s = _base_mime(str(mime or ""))
    if mime_s not in IMAGE_MIME_EXT:
        return {"type": "text", "text": f"[{label} omitted: unsupported MIME type {mime_s or 'unknown'}]"}
    raw = _decode_base64(data_b64)
    if raw is None:
        return {"type": "text", "text": f"[{label} omitted: invalid base64]"}
    if not raw:
        return {"type": "text", "text": f"[{label} omitted: empty image]"}
    if len(raw) > MAX_IMAGE_BYTES:
        return {"type": "text",
                "text": f"[{label} omitted: {len(raw)} bytes exceeds {MAX_IMAGE_BYTES} byte limit]"}
    try:
        path = _save_bytes(raw, prefix=TEMP_IMAGE_PREFIX, suffix=IMAGE_MIME_EXT[mime_s],
                           store_dir=store_dir)
    except OSError as e:
        return {"type": "text", "text": f"[{label} could not be saved: {type(e).__name__}: {e}]"}
    return {"type": "image", "mimeType": mime_s, "path": path,
            "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def binary_part(data_b64: Any, mime: Any, uri: str, *, label: str = "binary resource",
                store_dir: str | None = None) -> dict:
    raw = _decode_base64(data_b64)
    if raw is None:
        return {"type": "text", "text": f"[{label} {uri} omitted: invalid base64]"}
    if len(raw) > MAX_BINARY_BYTES:
        return {"type": "text",
                "text": f"[{label} {uri} omitted: {len(raw)} bytes exceeds {MAX_BINARY_BYTES} byte limit]"}
    mime_s = _base_mime(str(mime or "application/octet-stream")) or "application/octet-stream"
    if _is_text_mime(mime_s):
        return {"type": "text", "text": raw.decode("utf-8", errors="replace")}
    audio = mime_s.startswith("audio/")
    suffix = mimetypes.guess_extension(mime_s) or ".bin"
    try:
        path = _save_bytes(raw, prefix=TEMP_RESOURCE_PREFIX, suffix=suffix,
                           store_dir=store_dir)
    except OSError as e:
        return {"type": "text", "text": f"[{label} {uri} could not be saved: {type(e).__name__}: {e}]"}
    note = ("; the configured model has no audio input support" if audio else "")
    return {"type": "text",
            "text": f"[{label} {uri} ({mime_s}, {len(raw)} bytes) saved to {path}{note}]"}


def resource_parts(contents: list[dict] | None, *, server: str, uri: str = "",
                   labels: bool = False, store_dir: str | None = None) -> list[dict]:
    """resources/read contents -> ordered text/image parts."""
    out: list[dict] = []
    image_count = 0
    for item in contents or []:
        if not isinstance(item, dict):
            out.append({"type": "text", "text": "[unsupported resource content]"})
            continue
        cur_uri = str(item.get("uri") or uri)
        if labels and len(contents or []) > 1:
            out.append({"type": "text", "text": f"{cur_uri}:"})
        if isinstance(item.get("text"), str):
            out.append({"type": "text", "text": item["text"]})
        elif "blob" in item:
            mime = item.get("mimeType")
            if _base_mime(mime).startswith("image/"):
                if image_count >= MAX_IMAGES_PER_RESULT:
                    part = {"type": "text",
                            "text": f"[image {cur_uri} omitted: more than {MAX_IMAGES_PER_RESULT} images]"}
                else:
                    image_count += 1
                    part = image_part(item.get("blob"), mime, label=f"image {cur_uri}",
                                      store_dir=store_dir)
            else:
                part = binary_part(item.get("blob"), mime, cur_uri, store_dir=store_dir)
            out.append(part)
        else:
            out.append({"type": "text", "text": f"[unsupported resource content {cur_uri}]"})
    return out


def _resource_link_text(block: dict, server: str) -> str:
    uri = str(block.get("uri") or "")
    name = str(block.get("name") or "")
    title = str(block.get("title") or "")
    mime = str(block.get("mimeType") or "")
    size = block.get("size")
    details = ", ".join(x for x in (mime, f"{size} bytes" if isinstance(size, int) else "") if x)
    desc = f": {block.get('description')}" if block.get("description") else ""
    shown = title or name or uri
    return (f"[Resource {uri} \"{shown}\"{f' ({details})' if details else ''}{desc}; "
            f"read it with read_mcp_resource (server \"{server}\", uri \"{uri}\")]")


def content_parts(content: list[dict] | None, *, server: str,
                  store_dir: str | None = None) -> list[dict]:
    """MCP CallToolResult.content -> ordered internal parts."""
    out: list[dict] = []
    image_count = 0
    for block in content or []:
        if not isinstance(block, dict):
            out.append({"type": "text", "text": "[unsupported MCP content]"})
            continue
        kind = block.get("type")
        if kind == "text":
            text = block.get("text")
            out.append({"type": "text", "text": text if isinstance(text, str) else ""})
        elif kind == "image":
            if image_count >= MAX_IMAGES_PER_RESULT:
                out.append({"type": "text",
                            "text": f"[image omitted: more than {MAX_IMAGES_PER_RESULT} images]"})
            else:
                image_count += 1
                out.append(image_part(block.get("data"), block.get("mimeType"),
                                      store_dir=store_dir))
        elif kind == "audio":
            raw = _decode_base64(block.get("data"))
            if raw is None:
                out.append({"type": "text", "text": "[audio omitted: invalid base64]"})
            else:
                try:
                    path = _save_bytes(raw, prefix=TEMP_RESOURCE_PREFIX, suffix=".audio",
                                       store_dir=store_dir)
                    out.append({"type": "text",
                                "text": f"[audio {block.get('mimeType') or 'unknown'} saved to {path}; "
                                        "the configured model has no audio input support]"})
                except OSError as e:
                    out.append({"type": "text", "text": f"[audio could not be saved: {e}]"})
        elif kind == "resource_link":
            out.append({"type": "text", "text": _resource_link_text(block, server)})
        elif kind == "resource":
            res = block.get("resource")
            if isinstance(res, dict):
                rp = resource_parts([res], server=server,
                                    uri=str(res.get("uri") or ""), store_dir=store_dir)
                out.extend(rp)
            else:
                out.append({"type": "text", "text": "[unsupported embedded resource]"})
        else:
            out.append({"type": "text", "text": f"[unsupported MCP content {kind}]"})
    return out


def parts_text(parts: list[dict]) -> str:
    return "\n".join(str(p.get("text") or "") for p in parts if p.get("type") == "text")


def parts_images(parts: list[dict]) -> list[dict]:
    return [{k: p.get(k) for k in ("mimeType", "path", "bytes", "sha256")}
            for p in parts if p.get("type") == "image" and p.get("path")]


def bound_parts(parts: list[dict], *, max_bytes: int = MCP_OUTPUT_MAX_BYTES) -> dict:
    """限制模型可见文本；图片始终保留，并尽量保持原部件顺序。

    超长文本按原顺序逐块截断（最后一块用中间截断）；图片不会被移到文本后面
    或被丢弃。完整文本写入临时文件。
    """
    text = parts_text(parts)
    images = parts_images(parts)
    if len(text.encode("utf-8")) <= max_bytes:
        return {"text": text, "parts": parts, "images": images,
                "truncated": False, "full_output_path": None}
    full_text = text
    mid = truncate_middle(full_text, max_bytes=max_bytes)
    budget = max_bytes
    bounded_parts: list[dict] = []
    for part in parts:
        if part.get("type") != "text":
            bounded_parts.append(part)
            continue
        raw = str(part.get("text") or "")
        if budget <= 0:
            continue
        if len(raw.encode("utf-8")) <= budget:
            bounded_parts.append(part)
            budget -= len(raw.encode("utf-8"))
        else:
            bounded_parts.append({"type": "text",
                                  "text": truncate_middle(raw, max_bytes=budget)["content"]})
            budget = 0
    full_path = None
    try:
        full_path = save_temp_text(full_text, prefix="fwcore-mcp", suffix=".txt")
        where = f"[Full output: {full_path} (read it with offset/limit)]"
    except OSError as e:
        where = f"[Could not save full MCP output: {type(e).__name__}: {e}]"
    tokens = (mid["total_bytes"] + 3) // 4
    warning = (f"Warning: truncated output (original token count: {tokens})\n"
               f"Total output lines: {mid['total_lines']}\n\n")
    # 警告是元信息，不参与原始内容顺序；放在最前，图片/正文顺序仍保留。
    bounded_parts.insert(0, {"type": "text", "text": warning})
    bounded_text = parts_text(bounded_parts) + f"\n\n{where}"
    return {"text": bounded_text, "parts": bounded_parts, "images": images,
            "truncated": True, "full_output_path": full_path}


def image_data_uri(part: dict, *, max_bytes: int = MAX_IMAGE_BYTES) -> str | None:
    """模型调用时把内部 image part 读回 data URI；缺失/超限返回 None。"""
    path = part.get("path")
    mime = _base_mime(str(part.get("mimeType") or ""))
    if not isinstance(path, str) or mime not in IMAGE_MIME_EXT:
        return None
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes + 1)
    except OSError:
        return None
    if not data or len(data) > max_bytes:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def parts_json(parts: list[dict]) -> list[dict]:
    """给持久化/UI 的部件摘要：不含 base64，不含大段重复文本。"""
    out = []
    for p in parts:
        if p.get("type") == "image":
            out.append({k: p.get(k) for k in ("type", "mimeType", "path", "bytes", "sha256")})
        else:
            out.append(p)
    return out
