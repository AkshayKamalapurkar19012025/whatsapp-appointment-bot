# Working agreements

- **Before pushing any commit to a branch tied to an existing PR, verify
  the PR isn't already merged/closed** (check via the GitHub API, not
  just local git state). A branch's PR can merge while work is still in
  flight; pushing more commits to it afterward strands those commits on
  a closed PR instead of landing them. If the PR did merge, don't reuse
  that branch for new work — rebase any not-yet-merged commits onto the
  latest default branch and push them under a new branch name (or a
  force-pushed reset of the old one, only with explicit approval, since
  that's a destructive git operation).
