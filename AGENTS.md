# Commit conventions

- Make exactly one commit per new feature or fix.
- Split unrelated changes into separate commits, even when they are done
  from the same prompt.
- The subject line carries the key information and describes the change
  in fewer than 60 characters; include how it changes behavior when
  space permits.
- Keep commit messages brief: 5–20 lines, with lines no longer than
  71 characters.
- Every commit message body MUST include an **Implementation and
  verification summary**: concise bullets describing the implemented
  behavior and verification performed. State important verification
  limitations when applicable.
- Commit message bodies MUST use actual line breaks. NEVER pass literal
  `\n` text inside a quoted `-m` argument; use separate `-m` arguments or
  `git commit -F-` with a quoted heredoc.
- After each commit, test the program and confirm it remains executable
  and usable.
