# -*- coding: utf-8 -*-
"""lark-listen 公共常量与日志。全部走 stderr，绝不污染 stdout。"""

import os
import sys
from datetime import datetime, timezone, timedelta

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
LARK_CLI  = os.environ.get("LARK_CLI", "lark-cli")
EVENT_KEY = "im.message.receive_v1"
REACT_DEFAULT = "OnIt"   # 收到命中消息后默认点的表情（"在做了"）

CN_TZ = timezone(timedelta(hours=8))

LARK_ENV = {
    "PATH": os.environ.get("PATH", ""),
    "HOME": os.environ.get("HOME", "/root"),
    "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
    "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
}


# --------------------------------------------------------------------------- #
# 日志（全部走 stderr，绝不污染 stdout）
# --------------------------------------------------------------------------- #
def log(tag: str, msg: str, workdir: str = ""):
    ts = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{tag}] {msg}"
    print(line, file=sys.stderr, flush=True)
    if workdir:
        try:
            os.makedirs(workdir, exist_ok=True)
            with open(os.path.join(workdir, "listen.log"), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
