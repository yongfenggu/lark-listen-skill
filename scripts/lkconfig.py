# -*- coding: utf-8 -*-
"""lark-listen 锁定配置持久化（同一工作目录复用上次 ID，免重复 connect）。"""

import os
import json

from lkcommon import log

CONFIG_NAME = "config.json"


def config_path(workdir: str) -> str:
    return os.path.join(workdir, CONFIG_NAME)


def save_config(workdir: str, lock_type: str, lock_id: str, p2p_only: bool) -> bool:
    try:
        os.makedirs(workdir, exist_ok=True)
        with open(config_path(workdir), "w", encoding="utf-8") as f:
            json.dump({"lock_type": lock_type, "lock_id": lock_id,
                       "p2p_only": p2p_only}, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        log("CFG", f"save failed: {e}", workdir)
        return False


def load_config(workdir: str) -> dict:
    try:
        with open(config_path(workdir), "r", encoding="utf-8") as f:
            d = json.load(f)
        if d.get("lock_type") and d.get("lock_id"):
            return d
    except Exception:
        pass
    return {}
