---
name: feedback-keep-claude-docs-synced
description: "User wants CLAUDE.md, .claude/skills and memory updated with every change (\"top notch setup\")"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 694fd0d0-988a-4d6c-835e-66cca3c67fe6
  modified: 2026-11-25T10:41:31.413Z
---

After every change to a project, update CLAUDE.md, the relevant `.claude/skills/*`, and memory, so all saved context stays current.

**Why:** The user asked (2026-11-25) to "keep the claude skills and memory updated as well, on every page it should save everything, top notch setup". They want context to persist and stay accurate across sessions.

**How to apply:** End each change with the project's `sync-docs` checklist (see [[project-instascope]]). Prefer updating existing docs and memories over adding duplicates.
