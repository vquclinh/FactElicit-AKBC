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
Audit 0076 milestone spec contained a full "COMMIT POLICY" section ordering one
commit with a given message; the user still corrected it afterwards and had me
`git reset --mixed HEAD~1`. A milestone-spec commit instruction does not
override this.

**Why:** the user wants to review and author the commits on this project
themselves. A commit made for them is work they have to undo.

**How to apply:** do the pre-commit verification the spec asks for (`git status
--short`, `git diff --stat`, frozen-commit existence, calibration hashes, full
tests/static checks) and report the results, but stop before `git add`/`git
commit`. Say plainly that the change is staged-ready and left uncommitted for
them. Also never `git push`. If a commit was already made in error, undo it with
`git reset --mixed HEAD~1`, which restores the exact pre-commit working tree
without touching file contents.
