---
name: never-commit-user-commits-themselves
description: Never run git commit in this repo — the user commits all work themselves, even when a milestone spec says to commit
metadata:
  type: feedback
---

Never run `git commit` in the FactElicit-AKBC repo. The user commits everything
themselves. Finish the work, leave it in the working tree, run the verification
steps, and report — then stop.

This holds **even when the task text explicitly instructs committing**. The
Audit 0076 spec had a full "COMMIT POLICY" section ordering one commit with a
given message; the user still corrected it afterwards and had me
`git reset --mixed HEAD~1`. Audit 0078, 0079 and 0080 each repeated the
instruction, and the user reaffirmed the rule **twice more** — the second time
even though I had not committed and had explicitly handed them the command.

**Why:** the user wants to review and author every commit on this project.

**How to apply:** do the pre-commit verification the spec asks for (`git status
--short`, `git diff --stat`, frozen-commit existence, calibration hashes, full
tests/static checks) and report the results, then stop before `git add`/`git
commit`. Never `git push` either.

Say plainly and **early** — in the opening line of the report, not in a closing
section — that nothing was committed. Presenting a `git commit` command under a
prominent heading late in a long report reads as if the commit is imminent; that
is what drew the second correction. Keep the suggested command short, put it
last, and label it as theirs to run.

When work spans several milestones the user may commit mid-turn; re-read
`git rev-parse HEAD` before assuming the tree is still dirty. In audit 0080 they
committed while I was writing, which produced the frozen `SOURCE_SHA` the Colab
runbook needed.

If a commit was already made in error, undo it with `git reset --mixed HEAD~1`,
which restores the exact pre-commit working tree without touching file contents.
