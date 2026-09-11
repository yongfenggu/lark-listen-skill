# -*- coding: utf-8 -*-
"""lark-listen 提炼一行输出（给上层 Claude 看）。"""

FIELDS_OUT = ("message_id", "chat_id", "chat_type", "sender_id", "sender_type",
              "message_type", "content", "create_time", "reply_to", "root_id",
              "thread_id", "event_id", "mentions")


def project(evt: dict) -> dict:
    out = {k: evt.get(k, "") for k in FIELDS_OUT}
    # mentions 精简成 [{id, name}] 形式，去掉占位 key
    m = evt.get("mentions")
    if isinstance(m, list) and m:
        out["mentions"] = [{"id": x.get("id", ""), "name": x.get("name", "")}
                           for x in m if isinstance(x, dict)]
    else:
        out["mentions"] = []
    return out
