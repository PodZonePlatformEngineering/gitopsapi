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
