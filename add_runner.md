# Adding a Runner to Aisema

This file is an instruction checklist for an AI or developer adding a new coding-agent runner to Aisema. Complete the information-gathering section before changing source code. Do not guess a runner's command syntax, storage layout, identifiers, or metadata schema.

## 1. Information to collect before implementation

The new runner is not ready for integration until every applicable item below has a verified answer. Use the runner's documentation, executable help, local sample data, and controlled launch/resume experiments. Record exact commands, paths, field names, and representative examples.

### 1.1 Identity and executable discovery

Collect:

- Canonical runner name used in Aisema's command column and `SUPPORTED_HARNESSES`.
- Executable name and any aliases.
- Exact command used to detect installation, normally equivalent to `shutil.which(<executable>)`.
- Whether the runner can be installed under a path or wrapper whose basename differs from the command used to launch it.
- Version command and the minimum version, if behavior differs by version.
- Whether the runner is optional, platform-specific, or unavailable in non-interactive shells.

Current Aisema behavior: installed runners are detected by executable lookup before discovery and new-session selection.

### 1.2 Resume command prototype

Collect the exact command that resumes an existing session. Document:

- Command prototype, including the session-ID selector and whether it is positional, `--resume`, `--session`, `--name`, `--id`, or another form.
- Whether the selector accepts `id`, `id=value`, quoting, or additional required flags.
- Required flags before and after the session ID.
- Whether the command differs between local, remote, project, or workspace sessions.
- A working example with a real-looking placeholder ID.
- The exact command Aisema must store and launch without reconstructing user-owned commands.

Examples already supported by Aisema:

```text
omp --resume <session-id>
cursor-agent --resume=<session-id>
opencode --session <session-id>
goose session resume --name <session-id>
```

For a normal new runner, the default generated form is `<runner> --resume <session-id>`. Add a runner-specific exception only when the runner's verified syntax requires it.

### 1.3 Session identifier rules

Collect:

- Where the canonical session ID is stored.
- Whether the ID is globally unique or scoped by project, account, workspace, or runner.
- Character set, case sensitivity, maximum length, and whether IDs contain spaces or shell-special characters.
- Whether the ID appears in filenames, directories, JSON fields, SQLite fields, process arguments, or terminal metadata.
- Filename fallback rules when the record does not contain an ID.
- How to distinguish a session ID from a title, process ID, workspace ID, or conversation ID.
- At least three representative IDs, including boundary or unusual forms if they exist.

A new ID shape may require changes to `session_manager/model.py` (`_ID_RE` and possibly `_ENTRY_RE`), not only discovery code.

### 1.4 Session storage paths and scope

Collect exact paths and precedence:

- Default session root.
- Environment variables or configuration overrides.
- XDG, platform, project, workspace, and account-specific variants.
- Whether paths contain encoded working directories or date/session partitions.
- File extensions and recursive/non-recursive traversal rules.
- Whether symlinks, deleted files, archived files, or lock files must be ignored.
- Whether the runner stores sessions in one file, one file per session, a directory tree, or a database.
- Whether reading is safe while the runner is actively writing.

For OMP, the current facts are:

```text
Agent root override: PI_CODING_AGENT_DIR
Default agent root:  ~/.omp/agent
Session root:       $PI_CODING_AGENT_DIR/sessions
                    or ~/.omp/agent/sessions
Session files:      recursive *.jsonl files
Recap database:     $PI_CODING_AGENT_DIR/history.db
                    or ~/.omp/agent/history.db
```

### 1.5 Session data format and metadata schema

Collect a minimal real sample, with sensitive content removed, and document:

- Encoding and record boundaries.
- JSON, JSONL, SQLite, text, or binary format.
- Record/type discriminator fields.
- Session ID field and fallback location.
- Title/name/description fields.
- User-message role and content fields.
- Timestamp fields and their units: seconds, milliseconds, microseconds, nanoseconds, or ISO-8601.
- Update-time source used for sorting.
- Malformed, partial, empty, or concurrently-written record behavior.
- Fields that must not be displayed or persisted.

For OMP, the current JSONL facts are:

- `{"type":"session","id":"...","title":"..."}` identifies the session and may provide a title.
- `{"type":"title","title":"..."}` may provide a title.
- `{"type":"message", ...}` records messages.
- User prompts are selected when the effective role is `user`; content is read from the nested message content or compatible record content/text fields.
- The latest extracted user prompt becomes the discovered description; the title is the fallback.
- JSONL file modification time is the fallback update timestamp.
- Record timestamps are normalized to nanoseconds when present.

### 1.6 Recaps, summaries, and inspection history

Determine whether the runner stores recaps, summaries, compaction records, or continuation notes separately from user queries. Collect:

- Storage path and database/table or file/record schema.
- Session-ID join key.
- Recap text field and whether it contains Markdown, bullets, or multiple lines.
- Creation/update timestamp and units.
- Ordering rules when recaps and user queries are displayed together.
- Whether the newest recap should be shown in an opening summary page.
- Whether old recaps can be missing, duplicated, or out of order.

For OMP, the current recap facts are:

```sql
history.db.session_recaps
    id         INTEGER PRIMARY KEY
    session_id TEXT
    cwd        TEXT
    recap      TEXT
    created_at INTEGER   -- Unix seconds
```

Aisema joins `session_recaps.session_id` to the JSONL session ID. It stores inspection items as `(timestamp, kind, text)`, where `kind` is `recap` or `query`, and orders them by timestamp for the `M` popup. The latest recap is shown below the description when the popup opens.

### 1.7 Active-session detection

Collect enough information to answer all of these:

- How a running process exposes the session ID in its argv or environment.
- The executable basename that identifies the runner process.
- Whether wrapper processes, child processes, or shell commands hide the ID.
- Whether an active session can be identified only by a terminal, pane, socket, lock, or metadata marker.
- How a terminal/session marker maps to a session ID and process terminal.
- Whether process matching must be restricted to the current user.
- What should happen when a process exits during a scan.

Current Aisema process behavior:

- Scans only current-user `/proc` entries.
- Builds one process/terminal snapshot per active-state refresh round and reuses it for all entries.
- Matches a session ID in the process command line together with the runner basename.
- Falls back to OMP terminal-session metadata when the ID is absent from argv.
- Reads `pts-*` marker files under `$PI_CODING_AGENT_DIR/terminal-sessions` or the default agent root.
- Uses the second marker-file line, whose JSONL filename stem ends in `_<session-id>`, to map the session to `/dev/pts/<number>`.
- Matches a current-user runner process attached to that terminal.

Do not scan every process once per session entry. Add one reusable snapshot or an equivalent indexed scan.

### 1.8 Launch, new-session, and tmux behavior

Collect:

- Exact command for starting a new session.
- Whether startup requires a project directory, stdin, environment variables, or an interactive terminal.
- How the newly created session ID becomes observable.
- How long discovery may take after launch.
- Whether the first process is a wrapper whose pane/process identity must be retained.
- Whether tmux should launch a detached window or focus an existing pane.
- Whether a running session can safely be focused instead of launched again.
- Failure and cancellation behavior.

Aisema's generic flow creates a temporary entry with the runner command, records known IDs, launches the command, polls discovery for a new ID, replaces the temporary command with the runner-specific resume command, and materializes the entry only when the user edits or changes it as specified by the existing TUI behavior.

### 1.9 Safety, ownership, and performance

Collect:

- Whether session files and databases belong to the current user only.
- Whether reading requires a read-only database connection or file lock.
- Whether secrets, prompts, credentials, or private paths may appear in metadata.
- Maximum expected number and size of session files.
- Whether discovery should be cached, bounded, throttled, or limited to the current user.
- Whether discovery should obey Aisema's `session_list_update_interval` or needs a separate explicit refresh path.
- Whether a scan can block keyboard interaction.
- Errors expected from permission changes, deleted files, malformed records, and concurrent writes.

Do not add broad host-wide process or terminal scans when a current-user path or ownership filter is available.

### 1.10 Evidence package required before coding

The AI must collect and preserve:

1. The executable help output relevant to session listing, resume, and new-session startup.
2. One sanitized session record or database schema plus representative rows.
3. One sanitized active-process argv example.
4. One sanitized terminal/pane metadata example if terminal fallback is required.
5. Exact default and override paths.
6. Exact timestamp and ID normalization rules.
7. A list of expected edge cases and how the runner behaves for each.
8. A proposed set of deterministic test fixtures.

If any required fact is unknown, stop implementation and state the missing fact instead of guessing.

## 2. Aisema areas to update when adding a runner

Use this checklist after the runner facts are verified. Update every applicable area; do not add a partial discoverer that cannot resume, detect activity, or be tested.

### 2.1 Discovery registration and normalization

Update `session_manager/discovery.py`:

- Add the canonical runner name to `SUPPORTED_HARNESSES` in the intended display/discovery order.
- Add the runner to `_discover_runner()`'s dispatch map.
- Implement a runner-specific discoverer or reuse `_generic_json_session()` only when the schema actually matches its contract.
- Add all verified storage roots, environment overrides, file extensions, recursion rules, and database locations.
- Extract the canonical session ID and implement a verified filename fallback only if needed.
- Extract title, description, latest user prompt, update timestamp, and all user queries.
- Normalize timestamps through `_timestamp_ns()` or extend it only for a verified new format.
- Add recap/summary joins and timestamp-ordered `inspection_items` when the runner has them.
- Return the exact `DiscoveredSession` fields: path, session ID, description, update time, runner, resume command, user queries, and inspection items.
- Keep read failures, malformed records, missing IDs, and concurrent writes non-fatal.
- Preserve global sorting and selector limits in `discover_recent()`.

### 2.2 Resume command and session-file parsing

Update `session_manager/discovery.py` and, only when required, `session_manager/model.py`:

- Add a runner-specific `_resume_command()` branch if its syntax is not `<runner> --resume <id>`.
- Update `_ENTRY_RE` for a genuinely new command shape.
- Update `_ID_RE` for a genuinely new selector syntax or ID boundary.
- Verify `SessionEntry.runner`, `session_id`, `short_session_id`, serialization, and exact-command launching.
- Do not rewrite existing stored commands or introduce a second incompatible command convention.

### 2.3 Active process and terminal matching

Update `session_manager/process.py` when the new runner needs special handling:

- Keep current-user process filtering.
- Reuse the one-scan `ProcessSnapshot` for all entries in a refresh round.
- Add runner aliases or wrapper-process matching only when verified by process examples.
- Add terminal/pane metadata parsing only when argv matching cannot reliably identify active sessions.
- Cache terminal lookups inside the refresh snapshot.
- Handle disappearing processes and unreadable `/proc` entries without failing the TUI.

### 2.4 TUI and launch/new-session integration

Update `session_manager/tui.py` when needed:

- Ensure the runner appears in the new-session picker through `available_harnesses()`.
- Add runner-specific startup, ID polling, or post-launch discovery only when generic behavior is insufficient.
- Preserve temporary-entry, `N` marker, materialization, focus, and duplicate-launch behavior.
- Ensure `M` inspection data displays ordered queries/recaps without blocking normal keyboard input.
- Avoid synchronous repeated scans or runner-specific work on every draw.

Update `session_manager/__main__.py` only when CLI startup discovery needs runner-specific handling. The normal selector and active-session paths should remain generic.

### 2.5 Configuration integration

Add a configuration key only when the runner requires user-selectable behavior:

- Add it to `DEFAULTS` in `session_manager/config.py`.
- Add validation and a typed property when appropriate.
- Let `ConfigEditor` derive the key automatically from `DEFAULTS`.
- Ensure changing a commented value activates the line.
- Update README, `session_format.md`, and `session-manager-specs.md` defaults and behavior.

Do not add a runner-specific setting when executable lookup, standard paths, and generic discovery are sufficient.

### 2.6 Tests and fixtures

Update `tests/test_session_manager.py` with deterministic consumer-visible coverage for:

- Executable availability and runner registration.
- Resume-command generation and command parsing.
- Session ID extraction, including fallback and unusual IDs.
- Storage path discovery and path overrides.
- Metadata parsing, title/description fallback, latest prompt extraction, and timestamp normalization.
- Recap/query ordering and the `M` popup opening/latest-recap behavior, if applicable.
- Active process matching, wrapper handling, and terminal fallback, if applicable.
- New-session command, ID polling, materialization, and tmux focus, if applicable.
- Malformed, incomplete, permission-denied, and concurrently-written data.
- Discovery limits, global newest-first ordering, and duplicate handling.

Tests must assert observable behavior and boundaries. Do not add tests that only assert forwarding, copied source text, mock echoes, or that a collection is non-empty.

### 2.7 Documentation and examples

Update all relevant documentation together:

- `README.md`: supported runner list, invocation examples, discovery behavior, limitations, and keyboard behavior.
- `session_format.md`: user-visible discovery, descriptions, recaps/queries, launch behavior, and command examples.
- `session-manager-specs.md`: architecture, source paths, normalized fields, process detection, config, tests, and invariants.
- `add_runner.md`: add any newly discovered reusable onboarding facts only if they are general runner-integration guidance.

Document exact paths, command prototypes, data formats, fallback behavior, and known limitations. Do not document an unverified assumption as fact.

### 2.8 Verification before delivery

Run all applicable checks after every source change:

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q session_manager tests
```

Also run a real TUI startup/quit smoke test. For runner changes, exercise the actual discovery or launch path with sanitized fixtures or a controlled local runner. Confirm that:

- The program starts and remains keyboard-responsive.
- Existing runners behave unchanged.
- The new runner appears only when installed or explicitly tested.
- Session lines, descriptions, markers, recaps, and queries remain correctly ordered.
- No unrelated files, user data, or generated `__pycache__` files are included.
