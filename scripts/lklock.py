# -*- coding: utf-8 -*-
"""lark-listen 锁定模式：一次性抓取，判定锁定粒度。"""

import json
import subprocess
import threading

from lkcommon import LARK_CLI, EVENT_KEY, LARK_ENV, log
from lkconfig import save_config


def do_lock(timeout_sec: int, workdir: str) -> int:
    """抓一条消息，打印 {lock_type, lock_id, ...} 到 stdout。"""
    log("LOCK", f"一次性抓取，请在目标窗口发一条 connect（{timeout_sec}s 超时）", workdir)
    cmd = [LARK_CLI, "event", "consume", EVENT_KEY, "--as", "bot",
           "--max-events", "1", "--timeout", f"{timeout_sec}s"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.PIPE, text=True, bufsize=1, env=LARK_ENV)

    ready = threading.Event()
    def _drain():
        saw = False
        for line in proc.stderr:
            line = line.rstrip("\n")
            if not line:
                continue
            if not saw and line.startswith("[event] ready"):
                log("LOCK", f"ready", workdir)
                ready.set(); saw = True; continue
            log("LOCK-ERR", line, workdir)
    threading.Thread(target=_drain, daemon=True).start()
    ready.wait(timeout=60)

    out_line = ""
    for line in proc.stdout:
        out_line = line.strip()
        break
    rc = proc.wait()

    if not out_line:
        log("LOCK", f"未抓到消息（rc={rc}，超时或无事件）", workdir)
        print(json.dumps({"lock_type": "", "lock_id": "", "error": "no event captured"},
                         ensure_ascii=False), flush=True)
        return 1

    try:
        evt = json.loads(out_line)
    except Exception as e:
        log("LOCK", f"解析失败: {e}", workdir)
        print(json.dumps({"lock_type": "", "lock_id": "", "error": f"parse: {e}"},
                         ensure_ascii=False), flush=True)
        return 1

    ctype = evt.get("chat_type", "")
    if ctype == "group":
        lock_type, lock_id = "chat", evt.get("chat_id", "")
        p2p_only = False
    elif ctype == "p2p":
        # 单聊锁定天然只在私聊生效：自动 p2p_only=True，
        # 群聊消息一律忽略，不串到别的 session 在监听的群里。
        lock_type, lock_id = "user", evt.get("sender_id", "")
        p2p_only = True
    else:
        # 未知类型，默认按 chat_id 锁
        lock_type, lock_id = "chat", evt.get("chat_id", "")
        p2p_only = False

    result = {
        "lock_type": lock_type,
        "lock_id": lock_id,
        "chat_type": ctype,
        "p2p_only": p2p_only,
        "message_id": evt.get("message_id", ""),
        "sender_id": evt.get("sender_id", ""),
        "chat_id": evt.get("chat_id", ""),
        "content": evt.get("content", ""),
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    # 立刻持久化锁定 ID + p2p_only（单聊为 True，群聊为 False）
    save_config(workdir, lock_type, lock_id, p2p_only)
    log("LOCK", f"锁定 lock_type={lock_type} lock_id={lock_id}"
        f"{' (p2p_only)' if p2p_only else ''}（已存 config.json）", workdir)
    return 0
