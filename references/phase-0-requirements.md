# Phase 0: Requirements

From issue or user-described requirements: analyze, locate code, generate change plan, confirm with user.

## 0.1 Get Requirements

If from issue:
```bash
python3 ~/.claude/skills/vibe-pr/scripts/issue_parser.py --repo cann/hcomm --issue 123
# or URL
python3 ~/.claude/skills/vibe-pr/scripts/issue_parser.py --url https://gitcode.com/cann/hcomm/issues/123
```

Output JSON contains: title, description, labels, assignees, comments.

If from user: extract key information directly from conversation.

## 0.2 Analyze Requirements

Extract from the requirement:
- Problem symptom (what's broken / what feature is missing)
- Reproduction conditions (if bug)
- Expected behavior (what it should look like after fix)

## 0.3 Locate Code

Clone/update the target repo (if not local), locate relevant code:
- Search by error messages, function names, module names
- Read code to understand existing logic
- Identify files that need modification

## 0.4 Generate Change Plan

Present change plan to user:
- List of files to modify
- Intent of each modification
- Risk points (potential impact on other modules)

Wait for user confirmation before starting development.
Exception: trivial bugs (one-line fix, typo, obvious copy-paste error) can go straight to PR, notify after.
