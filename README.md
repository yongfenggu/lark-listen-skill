# lark-listen-skill

A [Claude Code](https://claude.com/claude-code) Skill that lets Claude listen to and respond to Lark (Feishu) messages — relay chat messages into the Claude session, reply back, and react with emojis.

## Structure

```
lark-listen/
├── SKILL.md                      # Skill definition / instructions
├── hooks/
│   └── session-end-cleanup.sh    # Cleanup hook run on session end
└── scripts/
    ├── listen.py                 # Entry: start the Lark message listener
    ├── lkconsume.py              # Consume events from the relay
    ├── lkbystander.py            # Bystander / context helpers
    ├── lkcommon.py               # Shared utilities
    ├── lkconfig.py               # Config persistence
    ├── lkfilter.py               # Message filtering
    ├── lkimage.py                # Image handling
    ├── lklock.py                 # Locking / concurrency
    ├── lkproject.py              # Project helpers
    └── lkreact.py                # Emoji reactions
```

## Install

Copy (or symlink) this directory into `~/.claude/skills/lark-listen/`, then invoke the `lark-listen` skill from Claude Code.

## Notes

- No secrets are baked in. Lark OAuth tokens are obtained at runtime via the MCP session.
- `__pycache__/` and `.pyc` files are ignored.
