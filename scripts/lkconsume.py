# -*- coding: utf-8 -*-
"""lark-listen 常驻监听循环。整合去重、旁观上下文。"""

import os
import json
import shlex
import signal
import subprocess
import threading

from lkcommon import LARK_CLI, EVENT_KEY, LARK_ENV, log
from lkreact import react_message
from lkproject import project
from lkfilter import is_hit, is_mention_hit
from lkbystander import push as bystander_push, drain as bystander_drain
from lkimage import fetch_images

# 孤儿看护：监听进程被外层 shell setsid 脱离了 Claude 的进程组，Claude 退出时内核不会
# 给它发 SIGHUP，会残留成僵尸继续监听。这里靠环境变量 CLAUDE_PID（Claude Code 启动
# 子进程时注入）做父子存活探测：父 Claude 没了，监听进程主动终止自己，随 session 生灭。
CLAUDE_PID_ENV = "CLAUDE_PID"
_ORPHAN_CHECK_INTERVAL = 3  # 秒


def _parent_alive() -> bool:
    """父 Claude 进程是否还存活。CLAUDE_PID 缺失视为无父（不启用看护，保持兼容）。"""
    pid_s = os.environ.get(CLAUDE_PID_ENV, "")
    if not pid_s:
        return True  # 没有父进程信息 → 不做看护，视为"活着"以免误杀
    try:
        pid = int(pid_s)
    except ValueError:
        return True
    try:
        os.kill(pid, 0)  # 信号 0：进程不存在则抛 ProcessLookupError
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 进程存在但无权探测，按活着处理
    except OSError:
        return True


def _start_orphan_watchdog(proc: subprocess.Popen, workdir: str) -> None:
    """后台线程：父 Claude 消失则终止整个监听进程，避免残留僵尸。

    用 os._exit 直接退出而非仅 terminate 子进程——consume_loop 主线程可能卡在
    读 stdout 上，proc.terminate 后主循环的 while-True 重启逻辑可能反复拉起。
    看护是最后保底，无需优雅退出，强退最可靠。
    """
    def _watch():
        while True:
            if not _parent_alive():
                log("GUARD", f"父进程 (CLAUDE_PID={os.environ.get(CLAUDE_PID_ENV)}) 已消失，"
                    f"终止监听以随 session 退出", workdir)
                try:
                    proc.terminate()
                except Exception:
                    pass
                # 给子进程一点清理时间再强退整个 Python 进程
                threading.Event().wait(1)
                os._exit(0)
                return
            threading.Event().wait(_ORPHAN_CHECK_INTERVAL)
    threading.Thread(target=_watch, daemon=True).start()


def consume_loop(lock_type: str, lock_id: str, workdir: str,
                 react_emoji: str, max_events: int, timeout_sec: int,
                 p2p_only: bool = False, mention_only: bool = False,
                 bot_id: str = "", bystander: bool = False) -> int:
    out_path = os.path.join(workdir, f"{lock_id}.out")
    os.makedirs(workdir, exist_ok=True)

    # 清空旧的输出文件（新一轮监听）
    try:
        open(out_path, "w").close()
    except Exception:
        pass

    cmd = [LARK_CLI, "event", "consume", EVENT_KEY, "--as", "bot"]
    if max_events > 0:
        cmd += ["--max-events", str(max_events)]
    if timeout_sec > 0:
        cmd += ["--timeout", f"{timeout_sec}s"]
    log("BUS", f"start: {' '.join(shlex.quote(c) for c in cmd)}", workdir)
    log("BUS", f"监听中 lock_type={lock_type} lock_id={lock_id} -> {out_path}"
        f"{' +bystander' if bystander else ''}", workdir)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,   # 保持 stdin 打开，避免 unbounded 模式 EOF 退出
        text=True,
        bufsize=1,
        env=LARK_ENV,
    )

    ready = threading.Event()
    # 孤儿看护：父 Claude 退出则终止本监听进程，避免残留僵尸
    _start_orphan_watchdog(proc, workdir)
    def _drain_stderr():
        saw = False
        for line in proc.stderr:
            line = line.rstrip("\n")
            if not line:
                continue
            if not saw and line.startswith("[event] ready"):
                log("BUS", f"ready: {line}", workdir)
                ready.set(); saw = True; continue
            log("BUS-ERR", line, workdir)
    threading.Thread(target=_drain_stderr, daemon=True).start()

    if not ready.wait(timeout=60):
        log("BUS", "WARN: ready marker not seen in 60s", workdir)
    else:
        log("BUS", "consuming…", workdir)
        # 就绪通知由触发本 skill 的 Claude 会话主动发送（lark-cli im +messages-send），
        # 脚本不硬编码自动发——保持"脚本零智能、只过滤+中继"的设计。
        log("BUS", "就绪，等待 Claude 发送就绪通知", workdir)

    n = 0
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError as e:
            log("PARSE", f"bad json: {e} line={line[:200]}", workdir)
            continue
        # 忽略 bot 自己发的消息，避免自回环
        if evt.get("sender_type") == "bot":
            continue

        hit = is_hit(evt, lock_type, lock_id, p2p_only)
        # --mention-only：命中后再过一层
        mention_ok = True
        if mention_only:
            mention_ok = is_mention_hit(evt, bot_id)

        # 旁观路径：群聊 + bystander 开 + 非命中(或非@) → 缓冲，不中继
        # p2p 单聊 lock_type=user 时 is_hit 对 owner 全 true，自然走命中路径
        if bystander and lock_type == "chat" and (not hit or not mention_ok):
            bystander_push(workdir, lock_id, evt)
            log("BYSTANDER", f"buffer mid={evt.get('message_id','')}", workdir)
            continue

        if not hit:
            continue
        if not mention_ok:
            log("FILTER", f"drop non-mention mid={evt.get('message_id','')}", workdir)
            continue

        # 命中：先异步点表情，再写 stdout
        mid = evt.get("message_id") or evt.get("id") or ""
        if react_emoji and mid:
            threading.Thread(target=lambda: react_message(mid, react_emoji, workdir),
                             daemon=True).start()
        out = project(evt)
        # 自动下载图片资源：命中消息若带图，下载到 /tmp 并把本地路径附到 images 字段，
        # 供上层 Claude 直接 Read 查看（免去再手动调 lark-cli 下载一步）。
        try:
            imgs = fetch_images(evt, workdir)
            if imgs:
                out["images"] = imgs
        except Exception as e:
            log("IMG", f"fetch_images exception mid={mid}: {e}", workdir)
        # 旁观上下文注入（命中时把缓冲的旁观消息附进来，并清空缓冲）
        if bystander:
            ctx = bystander_drain(workdir, lock_id)
            if ctx:
                out["bystander_context"] = ctx
        out_str = json.dumps(out, ensure_ascii=False)
        # 同时写 stdout 和文件，供 Claude Monitor 两种方式读取
        print(out_str, flush=True)
        try:
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(out_str + "\n")
        except Exception:
            pass
        n += 1
        log("RELAY", f"-> {out_path} mid={mid}"
            f"{' +bystander_ctx' if out.get('bystander_context') else ''}", workdir)

    rc = proc.wait()
    log("BUS", f"consume exited rc={rc} relayed={n}", workdir)
    return rc


# 就绪通知已移除：监听启动成功后由触发本 skill 的 Claude 会话主动用
# lark-cli im +messages-send 发送就绪消息，脚本不再硬编码自动发送。
# 保持脚本"零智能、只过滤+中继"的设计原则。
