# Adding a Runner to Aisema

A runner is a trusted, user-local Python plugin. Add a single module to
`~/.config/aisema/runners/` and restart Aisema; do not edit Aisema's discovery,
parser, process, CLI, or TUI code. The seven built-ins use the same API as external
plugins. A working illustrative adapter is in
[`examples/runner_template.py`](examples/runner_template.py).

**Security:** importing a plugin executes Python with your user's privileges.
Plugins are not sandboxed. Review the complete module and its dependencies before
installing it. Install proprietary dependencies outside Aisema; Aisema neither
installs packages nor verifies third-party code.

## 1. Verify the harness facts first

Do not guess a harness's command syntax, storage layout, identifiers, or schema.
Use official documentation, executable help, sanitized local data, and controlled
new/resume experiments to establish:

- **Identity and installation:** canonical runner name; every executable/wrapper
  basename it owns; actual installation detection; version-dependent behavior.
- **Commands:** exact new-session and resume commands, required subcommands/flags,
  command/description boundary, quoting rules, invalid selectors, and ID grammar.
  Establish how a new ID becomes observable after launch.
- **IDs:** canonical field, scope and stability, character set, case sensitivity,
  verified filename fallback, and distinction from workspace/process/pane IDs.
- **Storage:** default roots, environment overrides and precedence, recursion,
  extensions, account/project partitions, and safe read-only database access.
- **Records:** sanitized schema and sample records; ID/title/prompt fields;
  timestamp units and fallbacks; malformed, partial, or disappearing data behavior.
- **Inspection:** all user-query fields and optional recap sources, session join
  keys, and chronological ordering. Omit secrets and credentials from displayed
  metadata.
- **Activity:** sanitized current-user argv examples; wrapper/child behavior;
  any terminal/metadata mapping needed when the session ID is absent from argv.
- **Lifecycle and cost:** launch requirements, delayed ID creation, expected scan
  sizes, concurrent writes, permission errors, and whether a scan blocks input.

Keep relevant help output, sanitized records/schema, argv and optional terminal
examples, exact paths, normalization rules, and deterministic fixtures with the
adapter's own development materials. If a required fact is unknown, obtain it
before implementing that behavior. No actual HARRI CLI, storage, or process
compatibility is supplied or claimed by this repository.

## 2. Create and install the module

1. Copy `examples/runner_template.py` to a working file outside the auto-loaded
   directory. Its `example` CLI and JSON schema are deliberately illustrative,
   not a production adapter.
2. Replace `ExampleRunner`, its canonical name/executable ownership, and its
   storage reader with verified harness behavior. Override only the hooks that
   need different behavior; inherited methods are working defaults.
3. Validate the adapter with isolated HOME/PATH, sanitized fixtures, and a real
   controlled launch/resume before installing it.
4. Create `~/.config/aisema/runners/` yourself and copy the finished module there,
   for example as `harri.py` for a verified company adapter. Restart Aisema.

Aisema reads directly contained `.py` files in sorted order, excluding filenames
whose stems start with `_`. It does not recurse, search the working directory,
modify `sys.path`, create the plugin directory, or hot-reload code. The location
remains `~/.config/aisema/runners` even with a custom `--config` path. A missing or
empty directory means built-ins only. An unreadable existing directory, import
failure, incompatible version, or invalid declaration fails startup clearly with
the offending path; it is not silently treated as a missing runner. `--help`
exits before plugin imports or configuration writes.

## 3. Public API version 1

Import from `session_manager.runner_api` and `session_manager.runner_helpers` at
module scope. Do not import model, discovery orchestration, process orchestration,
or the TUI at module scope. The API uses type-only references to the existing
process snapshot/match types to avoid import cycles.

Every module exports:

```python
RUNNER_API_VERSION = 1
RUNNER = YourConcreteRunner()
```

`RUNNER_API_VERSION` must be the integer `1`, and `RUNNER` must be a concrete
instance of `Runner` with an implemented `discover()` method.

### Identity, ownership, availability, and ordering

- `name: str`: stable, nonempty canonical registry/session identity, not a display
  label. Deduplication uses `(runner, session_id)`.
- `command_names: tuple[str, ...]`: nonempty unique executable/wrapper basenames
  owned by this runner, including the executable starting its new-session
  command. Names and executable ownership must not conflict with another plugin
  or a built-in. Plugins cannot replace built-ins.
- `order: int = 100`: registry order is `(order, name)`. Built-ins retain orders
  0, 2–6 for copilot, cursor-agent, opencode, omp, goose, codex respectively.
- `hidden_when_available: tuple[str, ...] = ()`: hide this runner in the
  new-session picker if any listed canonical runner is installed. This does not
  alter discovery, parsing, or identity.
- `is_available() -> bool`: defaults to `shutil.which(command_names[0])` without
  starting a harness. Override only for verified installation rules. Availability
  is checked afresh for automatic discovery and new-session choices. Unavailable
  runners still parse stored commands and support explicit runner discovery.

Cursor is a built-in example of executable aliases: its canonical name is
`cursor-agent`, its `command_names` are `('cursor-agent', 'agent')`, and its
availability override accepts either executable. Generated new/resume commands
prefer installed `cursor-agent`, fall back to installed `agent`, and default to
`cursor-agent` when neither is installed. The picker always uses the canonical
identity, and parsing preserves saved commands using either name.

Ownership is selected from the basename of the first whitespace-separated
command token. A canonical name may differ from the executable: a runner named
`example` could own `example-cli`. Named wrappers and custom subcommands/flags are
supported; arbitrary pipelines, environment-prefix commands, and quoted executable
paths containing spaces are not a command-identification protocol.

### Commands and boundary recognition

- `new_session_command() -> str`: defaults to `command_names[0]`. Override for
  verified extra startup arguments. Its first executable basename must be owned
  by the runner before a session ID exists.
- `resume_command(session_id: str) -> str`: defaults to the unquoted
  `<command_names[0]> --resume <session_id>` form.
- `match_resume(text: str) -> ResumeMatch | None`: defaults to the public neutral
  `match_option_resume(text)` grammar: `--resume ID`, `--resume=ID`, `--session ID`,
  and `--session=ID`, with the supported long-option tokens before/after the
  selector. This is not a general shell parser. The default ID is a single token.

Custom syntax belongs entirely in the plugin: override **both** generation and
recognition, including quoting/ID rules and the complete command boundary. Return
`ResumeMatch(command=<exact prefix of text>, session_id=<nonempty ID>)` or `None`.
Accept a complete command at end-of-input, as well as before the description.
Exclude the description and all command/description separating spaces from the
returned command. For a stored session line, Aisema additionally requires one or
more literal spaces after that prefix; it preserves the exact command, separator,
and user description. Missing IDs, invalid selectors, or bad boundaries must not
be guessed into launchable sessions.

A registered runner owns its parser's rejection: Aisema never overrides it with
the generic grammar. For unregistered executables only, the existing neutral
option grammar remains recognized. If a custom-syntax plugin is removed, its
saved lines remain visible and searchable but unparsed and launch-blocked.
Descriptions/markers may be edited without regenerating recognized commands.

### Discovery and normalization

`discover() -> list[DiscoveredSession]` reads verified local stores synchronously.
Missing stores return `[]`. Skip expected unreadable, malformed, partial, or
concurrently disappearing records using narrow exceptions. Do not hide arbitrary
programming/runtime failures as empty discovery or inactive sessions.

`DiscoveredSession` is a frozen dataclass with these fields:

```python
path: Path | None
session_id: str
description: str
updated_ns: int
runner: str
resume_command: str
user_queries: tuple[str, ...] = ()
inspection_items: tuple[tuple[int, str, str], ...] = ()
```

`path=None` supports non-file-backed stores. IDs must be stable and
nonempty, `runner` must equal `self.name`, and `resume_command` must be recognized
by the same runner. Timestamps are Unix nanoseconds. Retain all user queries and
sort inspection items ascending by timestamp, using `"query"` and `"recap"` kinds.
The `M` popup and the existing lifecycle consume these fields without plugin
branches.

Prefer the public helpers when their behavior actually matches your data:

- `make_session(runner, path, session_id, description, updated_ns,
  user_queries=(), inspection_items=())` fills canonical identity, generates the
  resume command, and supplies the `<runner> session` description fallback.
- `generic_json_session(runner, path, session_id=None, *, clean_prompt=None)`
  accepts JSON objects/lists or JSONL records, including `messages`, `history`,
  `turns`, or `records` arrays. It takes top-level `sessionId`/`id`, then record
  `sessionId`/`session_id`/`id`, then the filename stem, unless an explicit ID was
  supplied. It prefers the latest user prompt over title/name/description,
  preserves all prompts, and uses file mtime when timestamps are absent/invalid.
  Recognized timestamp keys are `updatedAt`, `lastActivity`, `updated_at`, and
  `timestamp`, in existing iteration order. Numeric values use magnitude-based
  seconds/milliseconds/microseconds/nanoseconds normalization; ISO timestamps are
  accepted. The optional callback cleans user-prompt text, not the whole record.
  Catch expected filesystem races around this helper where appropriate.
- `clean_text`, `user_prompt`, `timestamp_ns`, and `json_records` expose the same
  neutral text/role/timestamp/record behavior for a custom reader.

Do not force an unrelated proprietary schema into the generic reader. Construct
`DiscoveredSession` directly or use `make_session()` after validating your own
records. The template docstring supplies one concrete illustrative JSON record.

### Snapshot-based process customization

`find_process(session_id, snapshot) -> ProcessMatch | None` defaults to
`match_argv_process(command_names, session_id, snapshot)`. It checks owned names in
declared order, then each indexed candidate in snapshot order, retaining the
existing session-ID-substring-in-joined-argv behavior.

Aisema performs one current-user `/proc` scan per refresh, shares
`ProcessSnapshot.by_runner` and `command_lines`, and caches TTY lookups. If argv
matching is insufficient, override this hook in the plugin and first use the
default matcher. Load verified metadata lazily into
`snapshot.runner_data[self.name]`, including an empty result, so entries reuse it
within the refresh. Reuse snapshot process indexes and cached TTY access; do not
rescan `/proc` per entry or expand activity detection to other users. Return a
real `ProcessMatch` only when activity is established. Propagate unexpected hook
failures instead of reporting inactivity and risking duplicate launches. OMP's
terminal-marker fallback belongs only to the OMP runner, not other plugins.
Cursor's fallback checks open `~/.cursor/chats/<project>/<session-id>/store.db`
file descriptors and `-wal`/`-shm` sidecars of processes owned by `cursor-agent`
or `agent`, deduplicated by PID. The exact session-ID directory component
establishes ownership. It caches the mapping, including an empty result, in
`snapshot.runner_data['cursor-agent']` once per snapshot.

## 4. Existing lifecycle and verification

The picker calls the runner's exact new-session command. Aisema records known IDs,
polls discovery for the newest previously unseen ID, adopts its resume command,
and retains the temporary `N` entry without writing the session file. Description
editing materializes it only after an ID is available. Inspection, ordinary
refresh, last-used refresh, exact stored-command launching, and tracked tmux-pane
focus all use the existing runner-neutral lifecycle. No additional TUI branch,
central registration list, parser regex, or process dispatch branch is needed.

Use isolated HOME/PATH and deterministic sanitized fixtures to cover:

- Drop-in loading, unavailable executable versus absent plugin, actionable
  startup failures, conflicting ownership, and malformed command boundaries.
- Discovery metadata, canonical identity distinct from executable basename,
  same ID across different runners, global limits, and chronological inspection.
- Exact command/separator preservation on load, description/marker editing, and
  serialization; quoted IDs if supported by your custom grammar.
- Current-user activity, shared snapshots, metadata caching, disappearing data,
  and no false positive activity from another runner's metadata.
- Actual new/resume execution, delayed ID adoption, unchanged file before editing,
  persistence after editing, and tmux tracked-pane focus when tmux is available.

For Aisema core changes, run the existing suite and focused plugin regressions:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests python3 -m unittest -v test_runner_plugins
```

Also exercise a real CLI/TUI launch with disposable configuration and session
files. The repository's synthetic plugin fixtures prove the extension boundary,
not compatibility with any company's actual HARRI harness. Validate your real
adapter against the verified harness before distributing it.
