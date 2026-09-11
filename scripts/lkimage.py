# -*- coding: utf-8 -*-
"""lark-listen 图片资源自动下载。

命中消息里若带图片 resource（content 里的 ![Image](img_v3_xxx) 或 message_type=image），
自动调 lark-cli im +messages-resources-download 下载到 /tmp，把本地路径数组附到
中继消息的 images 字段，供上层 Claude 直接 Read 查看，免去手动下载一步。

保持"脚本零智能"原则：只做资源获取（下载），不做识别/判断/回复。
"""

import os
import re
import json
import subprocess

from lkcommon import LARK_CLI, LARK_ENV, log

# content 里图片 markdown 的 file_key 形如 img_v3_xxx；也兼容 file_xxx（文件资源）
_IMG_PATTERN = re.compile(r"!\[[^\]]*\]\((img_[A-Za-z0-9_\-]+)\)")
# /tmp 下载根目录（lark-cli denylist 禁止 /root 下，必须用 /tmp）
_IMG_DIR = "/tmp"


def extract_image_keys(content: str) -> list:
    """从消息 content（markdown）里提取图片 file_key 列表。"""
    if not content:
        return []
    return _IMG_PATTERN.findall(content)


def download_image(message_id: str, file_key: str, workdir: str) -> str:
    """下载单个图片 resource 到 /tmp，返回本地绝对路径；失败返回空串。"""
    out_base = os.path.join(_IMG_DIR, f"lark-img-{message_id}-{file_key}")
    cmd = [LARK_CLI, "im", "+messages-resources-download",
           "--message-id", message_id,
           "--file-key", file_key,
           "--type", "image",
           "--output", out_base,
           "--as", "bot"]
    try:
        r = subprocess.run(cmd, env=LARK_ENV, capture_output=True, text=True, timeout=30)
    except Exception as e:
        log("IMG", f"download exception key={file_key}: {e}", workdir)
        return ""
    if r.returncode != 0:
        log("IMG", f"download fail key={file_key} rc={r.returncode} {r.stderr.strip()[:200]}", workdir)
        return ""
    try:
        d = json.loads(r.stdout)
        if d.get("ok") and d.get("data", {}).get("saved_path"):
            return d["data"]["saved_path"]
    except Exception:
        pass
    # 退而求其次：直接看文件是否落盘
    if os.path.exists(out_base):
        return out_base
    # lark-cli 可能补了扩展名，找前缀匹配的
    for name in os.listdir(_IMG_DIR):
        if name.startswith(os.path.basename(out_base)):
            return os.path.join(_IMG_DIR, name)
    return ""


def fetch_images(evt: dict, workdir: str) -> list:
    """检测并下载消息里所有图片，返回本地路径列表。无图或全失败返回 []。"""
    content = evt.get("content", "") or ""
    mid = evt.get("message_id", "") or evt.get("id", "") or ""
    keys = extract_image_keys(content)
    if not keys:
        return []
    paths = []
    for k in keys:
        p = download_image(mid, k, workdir)
        if p:
            paths.append(p)
            log("IMG", f"downloaded key={k} -> {p}", workdir)
    return paths
