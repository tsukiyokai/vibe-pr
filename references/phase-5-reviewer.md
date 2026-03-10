# Phase 5: Find Reviewer + Respond to Reviews

## 5.1 Understand the Landscape

```bash
python3 ~/.claude/skills/vibe-pr/scripts/pr_status.py --repo cann/hcomm --pr <number>
```

Get: candidate list (`modules[].lgtm.candidates` / `approve.candidates`), current approval progress, latest commit time.

```bash
python3 ~/.claude/skills/vibe-pr/scripts/reviewer_activity.py --repo cann/hcomm --pr <number> --recent 30
```

Activity data is background info, not a shopping list:
- High review count = this person is already busy, not that they're always available
- Low review count != unwilling to help, maybe just nobody asked
- Fast response means they value the community, not that they're idle

## 5.2 Relationships Before Rankings

Ask user (proactively if they don't mention):
- Who have you reviewed code for before? (reciprocity is the strongest ask)
- Who have you interacted with at SIG meetings or mailing lists?
- Anyone who has reviewed for you before? (maintaining relationships > building new ones)

For new contributors with no community ties: suggest reviewing others' PRs first, showing up at SIG meetings, then asking for reviews. This is investment, not wasted time.

## 5.3 Recommendation Strategy

Combine relationships and data:
- lgtm needs 2, recommend 3 for redundancy; approve needs 1, recommend 2
- Prioritize candidates with existing interaction history
- Then look at who has context on this code module (from recent PRs)
- Activity ranking is last resort

## 5.4 How to Request Review

Principles for user:
- Don't mass-@. Two or three people is normal, five is spam
- Explain what changed and why. Reviewer's time > your wait time
- Keep PRs small. Nobody wants to review 500 lines; 50 lines gets approved quickly
- Timing: hccl SIG biweekly Fri 14:00 meeting; before/after meeting is good timing; don't push on weekends/holidays

If no response for a long time, ask yourself:
1. Is the PR too big? Can it be split?
2. Is the description clear enough? Can a reviewer understand in 30 seconds?
3. Have you reviewed for others recently?

## 5.5 Approval Rules

- Each module needs at least 2 `/lgtm` + 1 `/approve`
- `/approve` implies `/lgtm`
- Approval must be after latest commit time (new push invalidates old approvals)
- pr_status.py output `latest_commit_at` can check approval validity

Update context: record recommended reviewers and reasoning.

---

## 5.5 (bis): Review Response

When reviewer leaves comments, classify and act.

### Get Review Comments

```bash
# All review comments
python3 ~/.claude/skills/vibe-pr/scripts/comment_parser.py --repo cann/hcomm --pr <number>

# Only after latest push
python3 ~/.claude/skills/vibe-pr/scripts/comment_parser.py --repo cann/hcomm --pr <number> --since-commit
```

Check review status board:
```bash
python3 ~/.claude/skills/vibe-pr/scripts/review_tracker.py --repo cann/hcomm --pr <number> --summary
python3 ~/.claude/skills/vibe-pr/scripts/review_tracker.py --repo cann/hcomm --pr <number> --pending
```

### Dispatch review-responder agent

For batches of review comments, dispatch the review-responder agent:
```
Agent dispatch: review-responder
Input: {
  "repo": "<repo>",
  "pr": <number>,
  "repo_root": "<path>",
  "comments": [comment_parser output]
}
```

The agent returns:
- `fixed`: comments it resolved (with fix summary and reply drafts)
- `needs_user`: questions/design concerns (with suggested replies)

### After Agent Returns

- Show user the `fixed` summaries and ask to batch-send replies
- Show user the `needs_user` questions with suggested replies for editing
- If files were changed: commit, push, re-trigger CI
- Update review_tracker status for each comment

### Important Notes

- New push invalidates prior lgtm/approve -- need to re-request review
- Do not post replies on behalf of user (social actions must be by the user)
- Prepare reply content for user to copy/edit before sending
