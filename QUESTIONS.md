# Questions / Task Breakdowns for Trismagistus

**Purpose**: Claude Code writes questions, task breakdowns, and blockers here. Trismagistus monitors this file and responds via tasks.md updates or direct instructions.

---

## Template

```markdown
## [TASK-XXX] Task Name

**Status**: Blocked / Clarification needed / Breakdown proposed / Question

**Question/Breakdown**:
- Item 1
- Item 2

**Context**: Why this matters / what you've tried / what you need
```

---

## Active Questions

## [TASK-055] Archive Completed Tasks

**Status**: New (delegated from Trismagistus 2026-03-11 00:12 GMT)

**Action needed**:
1. Read `podzoneAgentTeam/planning/tasks.md` (old format)
2. Identify all ✅ Completed tasks
3. Move to `podzoneAgentTeam/planning/completed-tasks.md` under 2026-03-11 section
4. Format: Brief (task ID, completed date, agent, outcome only)

**Detail**: `podzoneAgentTeam/planning/INTER-AGENT-MESSAGING.md`

**Context**: Part of inter-agent messaging refactor. Clean context by archiving completed work.

---

## [TASK-056] Update Agent Personas with New Messaging Protocol

**Status**: New (delegated from Trismagistus 2026-03-11 00:12 GMT)

**Action needed**:
1. Update `podzoneAgentTeam/agents/claude-code/AGENT.md` (create if missing)
2. Update `gitopsapi/READMEFIRST.md` to reference new system:
   - Write tasks to `podzoneAgentTeam/agents/claude-code/trismagistus-tasks.md`
   - Read team tasks from `podzoneAgentTeam/planning/team-tasklist.md`
   - Detail files in `podzoneAgentTeam/agents/claude-code/details/`
3. Update `podzoneAgentTeam/agents/trismagistus/AGENT.md` if needed
4. Update `podzoneAgentTeam/agents/claude-web/AGENT.md` (create if missing)

**Detail**: `podzoneAgentTeam/planning/INTER-AGENT-MESSAGING.md`

**Context**: Document new inter-agent messaging system in agent personas.

---

## [TASK-029] GitOpsAPI Helm Chart — Harbor Push (Manual Upload Required)

**Status**: Blocked — Helm chart API push failing, needs manual UI upload (2026-03-10 23:30 GMT)

**Progress**:
- ✅ Harbor containers restarted on VM 1000 (192.168.4.100)
- ✅ Harbor project `gitopsapi` created (project_id: 9)
- ✅ Chart copied to freyr: `/tmp/gitopsapi-0.1.0.tgz`
- ❌ Helm chart push via API/cm-push failing (chartrepo endpoint errors)

**Workaround needed (Martin)**:
Manual upload via Harbor UI:
1. Login: http://192.168.4.100 (admin / Harbor12345)
2. Navigate: Library → gitopsapi project
3. Upload Chart button → select `/tmp/gitopsapi-0.1.0.tgz` (from freyr or local copy)

**Documentation created**: 
- `podzoneAgentTeam/infrastructure/harbor-docker-restart.md` — Harbor container management procedures
- **TASK-051** proposed: Automate Harbor restart on hypervisor boot (future infrastructure agent)

**Context**: Chart is built and linted. Harbor chartrepo API endpoint not functioning properly in Harbor v2.14.0. Chart source: `gitopsapi/charts/gitopsapi/` v0.1.0.

---

## Resolved

(Trismagistus will move resolved items here with answers.)

<!-- End of file -->
