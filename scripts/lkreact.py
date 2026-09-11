# -*- coding: utf-8 -*-
"""lark-listen 表情回复：命中即点，给用户即时视觉确认。"""

import json
import subprocess

from lkcommon import LARK_CLI, LARK_ENV, log


def react_message(message_id: str, emoji: str, workdir: str = "") -> bool:
    if not emoji or not message_id:
        return False
    data = json.dumps({"reaction_type": {"emoji_type": emoji}})
    try:
        r = subprocess.run(
            [LARK_CLI, "im", "reactions", "create",
             "--message-id", message_id, "--data", data, "--as", "bot"],
            capture_output=True, text=True, env=LARK_ENV, timeout=30,
        )
        if r.returncode == 0:
            return True
        msg = (r.stderr or r.stdout or "").strip().replace("\n", " ")[:160]
        log("REACT", f"fail emoji={emoji} mid={message_id} {msg}", workdir)
        return False
    except Exception as e:
        log("REACT", f"exception emoji={emoji}: {e}", workdir)
        return False
