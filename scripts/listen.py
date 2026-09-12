#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lark-listen — 通用飞书消息监听管道（skill 脚本，轻量、零智能）

两种模式：
  1) --lock             一次性抓一条消息，根据 chat_type 自动判定锁定粒度，
                        打印一行 JSON {lock_type, lock_id, ...} 到 stdout 后退出。
                        · p2p 单聊  → lock_type=user,  lock_id=sender_id
                        · group 群  → lock_type=chat,  lock_id=chat_id
  2) --lock-type {chat|user} --lock-id <id>
                        常驻监听：消费 im.message.receive_v1，按锁定 ID 过滤，
                        命中即转 stdout（NDJSON，一行一条），供上层 Claude 处理回复。
                        非命中静默丢弃。输出文件按 lock_id 命名，多实例互不干扰。

工程加固：
  - stale-pid 检查：启动时自动读 pid 文件,按 lock_id 精确判断旧进程是否存活,
    存活且监听同一 lock_id 则退出避免重复中继,已死则清理僵尸 pid 文件后正常启动。
  - event_id 去重：防事件流重放/多 Monitor 重复触发。
  - 旁观上下文（--bystander，群聊场景）：非命中消息缓冲，下次命中时注入只读上下文。

设计：纯过滤+中继，不做分类、不回复、不落盘非命中消息、无长期记忆。

用法：
  python3 listen.py --lock                                      # 抓一条锁定
  python3 listen.py --lock-type chat --lock-id oc_xxx            # 监听该群
  python3 listen.py --lock-type user --lock-id ou_xxx            # 监听该用户
    [--react OnIt] [--no-react] [--max-events N] [--timeout D]
    [--workdir <dir>]     # 默认 ./.lark-listen，输出写 <workdir>/<lock_id>.out
    [--bystander]         # 群聊旁观上下文（默认关闭）
"""

import os
import sys
import time
import atexit
import argparse

from lkcommon import REACT_DEFAULT, log
from lkconfig import save_config, load_config
from lklock import do_lock
from lkconsume import consume_loop


def _remove_pid_file(path: str) -> None:
    """进程退出时清理自己的 pid 文件（atexit 注册）。"""
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _check_stale_pid(pid_path: str, lock_id: str, workdir: str) -> None:
    """检查同 lock_id 是否已有监听进程在跑。

    读 pid 文件 → 验证进程存活 → 验证命令行含同一个 lock_id。
    - 进程存活且监听同一 lock_id:已有进程在跑,打印提示后 sys.exit(0) 避免重复中继。
    - 进程已死(僵尸 pid 文件):清理 pid 文件,让本次正常启动。
    - pid 文件不存在或指向别的 lock_id:无冲突,正常继续。

    这是 zero-shot 启动的关键:不靠 Claude 手动检查旧进程,脚本自动处理。
    """
    if not os.path.exists(pid_path):
        return
    try:
        with open(pid_path) as f:
            content = f.read().strip()
        parts = content.split()
        if len(parts) < 2:
            return
        old_pid = int(parts[-1])
    except (ValueError, OSError):
        return

    try:
        os.kill(old_pid, 0)
    except (ProcessLookupError, PermissionError):
        log("PID", f"清理僵尸 pid 文件:{pid_path} → 旧 PID {old_pid} 已退出", workdir)
        try:
            os.remove(pid_path)
        except Exception:
            pass
        return

    try:
        with open(f"/proc/{old_pid}/cmdline", "rb") as cf:
            cmdline = cf.read().replace(b"\x00", b" ").decode("utf-8", "replace")
    except (FileNotFoundError, ProcessLookupError, OSError):
        log("LOCK", f"已有进程 {old_pid} 监听 {lock_id},本次退出避免重复中继", workdir)
        sys.exit(0)

    if lock_id in cmdline and "listen.py" in cmdline:
        log("LOCK", f"已有进程 {old_pid} 监听 {lock_id},本次退出避免重复中继", workdir)
        sys.exit(0)
    else:
        log("PID", f"pid 文件指向进程 {old_pid} 不监听 {lock_id}(cmd: {cmdline[:80]}),清理后继续", workdir)
        try:
            os.remove(pid_path)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="lark-listen 通用飞书消息监听管道")
    ap.add_argument("--lock", action="store_true",
                    help="一次性抓一条消息，自动判定 lock_type/lock_id 后退出。")
    ap.add_argument("--lock-type", choices=["chat", "user"],
                    help="常驻监听模式：锁定类型。chat=按群，user=按人。")
    ap.add_argument("--lock-id", help="常驻监听模式：锁定的 chat_id(oc_) 或 user open_id(ou_)。")
    ap.add_argument("--react", dest="react_emoji", default=REACT_DEFAULT, metavar="EMOJI",
                    help=f"命中后自动点的表情 emoji_type（默认 {REACT_DEFAULT}）。")
    ap.add_argument("--no-react", dest="react_emoji", action="store_const", const="",
                    help="关闭自动表情回复。")
    ap.add_argument("--max-events", type=int, default=0,
                    help="常驻模式：收到 N 条后退出（0=无限）。")
    ap.add_argument("--timeout", type=int, default=0,
                    help="常驻/锁定模式：N 秒后退出（0=无限）。")
    ap.add_argument("--lock-timeout", type=int, default=120,
                    help="--lock 模式等待 connect 的超时秒数（默认 120）。")
    ap.add_argument("--workdir", default="",
                    help="输出目录（默认 ./.lark-listen）。")
    ap.add_argument("--p2p-only", action="store_true",
                    help="只接收 p2p 单聊消息，忽略群聊。user 锁定时自动置位（单聊锁定只在私聊生效）；"
                         "chat 锁定时默认关闭，可显式传入。")
    ap.add_argument("--relock", action="store_true",
                    help="忽略已有 config.json，强制重新 connect 锁定新目标。")
    ap.add_argument("--mention-only", action="store_true",
                    help="只中继 @bot 的消息（群聊常用：被 @ 才响应）。")
    ap.add_argument("--bot-id", default="",
                    help="bot 的 open_id（ou_），用于 --mention-only 判断是否 @ 了 bot。"
                         "为空时只要 mentions 非空即算命中。")
    ap.add_argument("--bystander", action="store_true",
                    help="群聊旁观上下文：非命中消息缓冲，下次命中时作为只读上下文注入。"
                         "默认关闭；单聊不受影响。")
    args = ap.parse_args()

    workdir = args.workdir or os.path.join(os.getcwd(), ".lark-listen")

    if args.lock:
        sys.exit(do_lock(args.lock_timeout, workdir))

    # 常驻监听模式：先看有没有显式给 ID
    if not args.lock_type or not args.lock_id:
        # 没给 ID：尝试读上次保存的 config（同一工作目录复用，免重复 connect）
        if not args.relock:
            cfg = load_config(workdir)
            if cfg:
                args.lock_type = cfg["lock_type"]
                args.lock_id = cfg["lock_id"]
                # config 里存的 p2p_only 作为默认，但命令行 --p2p-only 仍可叠加
                if cfg.get("p2p_only"):
                    args.p2p_only = True
                log("CFG", f"复用上次锁定 {cfg['lock_type']}={cfg['lock_id']}"
                    f"{' (p2p_only)' if args.p2p_only else ''}", workdir)
        if not args.lock_type or not args.lock_id:
            # 仍无 ID：提示用 --lock 先锁定
            ap.error("需要 --lock-type/--lock-id；首次请先 --lock 锁定目标"
                     "（已锁定过的目录会自动复用 .lark-listen/config.json）")
    else:
        # 显式给了 ID：以这次为准，更新 config（覆盖）
        # user 锁定强制 p2p_only=True（单聊锁定只在私聊生效，群聊交给 chat 锁定）
        if args.lock_type == "user":
            args.p2p_only = True
        save_config(workdir, args.lock_type, args.lock_id, args.p2p_only)

    # 无论来自命令行还是 config 复用，都以本次实际生效的参数为准刷新 config
    save_config(workdir, args.lock_type, args.lock_id, args.p2p_only)

    # 写 pid 文件：供 SessionEnd hook 按 session_id 精确清理本 session 启动的监听进程
    # 内容格式：「<CLAUDE_CODE_SESSION_ID> <PID>」，缺 session_id 时以「-」占位
    pid_path = os.path.join(workdir, f"{args.lock_id}.pid")
    _check_stale_pid(pid_path, args.lock_id, workdir)
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "-") or "-"
    try:
        with open(pid_path, "w") as f:
            f.write(f"{session_id} {os.getpid()}\n")
        atexit.register(lambda: _remove_pid_file(pid_path))
        log("PID", f"pid 文件已写 {pid_path} session={session_id} pid={os.getpid()}", workdir)
    except Exception as e:
        log("PID", f"写 pid 文件失败: {e}", workdir)

    rc = 0
    while True:
        try:
            rc = consume_loop(args.lock_type, args.lock_id, workdir,
                              args.react_emoji, args.max_events, args.timeout,
                              args.p2p_only, args.mention_only, args.bot_id,
                              args.bystander)
        except KeyboardInterrupt:
            log("MAIN", "interrupted, exit", workdir)
            break
        except Exception as e:
            log("MAIN", f"consume_loop exception: {e} — restart in 5s", workdir)
            time.sleep(5)
            continue
        break
    sys.exit(rc)

if __name__ == "__main__":
    main()
