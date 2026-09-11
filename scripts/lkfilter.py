# -*- coding: utf-8 -*-
"""lark-listen 命中判定。"""


def is_hit(evt: dict, lock_type: str, lock_id: str, p2p_only: bool = False) -> bool:
    if p2p_only and evt.get("chat_type", "") != "p2p":
        return False
    if lock_type == "chat":
        return evt.get("chat_id", "") == lock_id
    if lock_type == "user":
        return evt.get("sender_id", "") == lock_id
    return False


def is_mention_hit(evt: dict, bot_id: str = "") -> bool:
    """是否 @ 了 bot。bot_id 为空则只要 mentions 非空即算命中。"""
    m = evt.get("mentions")
    if not isinstance(m, list) or not m:
        return False
    if not bot_id:
        return True
    return any(isinstance(x, dict) and x.get("id", "") == bot_id for x in m)
