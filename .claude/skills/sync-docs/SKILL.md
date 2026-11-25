---
name: sync-docs
description: After-change checklist that keeps CLAUDE.md, .claude/skills, project memory, README and .env.example accurate. Use after any code change, or when the Stop hook says code is newer than the docs.
---

# Sync project knowledge

Go through each item; edit only what the change actually affected.

- [ ] **CLAUDE.md**: architecture table (new or renamed files), Instagram strategy (endpoints,
      auth needs, date verified), conventions, commands.
- [ ] **Skills** (`.claude/skills/*/SKILL.md`): are the steps still right? Add a skill for any new
      workflow you expect to repeat.
- [ ] **Memory** (`~/.claude/projects/-Users-muhammad-hamza-Desktop-untitled-folder/memory/`):
      update `project-instascope.md` with decisions, status and open issues, with absolute dates.
      Record new user preferences as `feedback`/`user` memories and keep `MEMORY.md` indexed.
- [ ] **README.md**: endpoint table, feature list, session/proxy table.
- [ ] **.env.example**: every field in `app/core/config.py::Settings` appears here with a comment.
- [ ] `make check` passes.

If nothing needs updating, say so in one line; the hook won't ask again for the same changes.
