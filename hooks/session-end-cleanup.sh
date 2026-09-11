#!/usr/bin/env bash
# lark-listen SessionEnd hook：清理本 session 启动的监听进程
#
# 触发：Claude Code session 结束时（正常退出 / /clear / /resume 切换）。
# 输入（stdin JSON）：{ session_id, cwd, hook_event_name, reason, ... }
# 逻辑：扫描 <cwd>/.lark-listen/*.pid，每行格式「<session_id> <pid>」，
#       只杀 session_id 与本次 session 匹配的进程，绝不动别的 session 的监听。
#       pid 文件随后清理（避免指向已回收的 PID）。
#
# 注意：这是 per-session 精确清理，不是 pkill 全杀。即使多个 session 在同一目录
#       各自监听，也只清自己起的那个。

set -u

# 从 stdin 读 hook JSON，提取 session_id 和 cwd
input="$(cat)"
session_id="$(printf '%s' "$input" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null || echo '')"
cwd="$(printf '%s' "$input" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null || echo '')"

# 兜底：JSON 没给 cwd 就用当前工作目录
[ -z "$cwd" ] && cwd="$(pwd)"

lark_dir="$cwd/.lark-listen"

# 没有监听目录或没有 pid 文件，直接退出（无可清理）
[ -d "$lark_dir" ] || exit 0
compgrep=$(ls "$lark_dir"/*.pid 2>/dev/null) || exit 0

killed=0
for pidfile in "$lark_dir"/*.pid; do
  [ -f "$pidfile" ] || continue
  # 文件内容：「<session_id> <pid>」
  read -r pid_session pid < "$pidfile" 2>/dev/null || continue
  # 只杀属于本次 session 的（session_id 精确匹配）
  [ "$pid_session" = "$session_id" ] || continue
  # 校验 pid 是数字
  [[ "$pid" =~ ^[0-9]+$ ]] || { rm -f "$pidfile"; continue; }
  # 校验该 pid 的命令确实是 listen.py（防 PID 复用误杀）
  if ps -o cmd= -p "$pid" 2>/dev/null | grep -q "listen.py"; then
    kill "$pid" 2>/dev/null && killed=$((killed+1))
  fi
  # 无论是否杀成功，清理这个 pid 文件（它只服务本次 session）
  rm -f "$pidfile"
done

# 清理完静默退出（SessionEnd hook 的 stdout 不会展示给用户）
exit 0
