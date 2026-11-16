# Aisema Specifications

This document records the implemented behavior and architecture of Aisema. It is intended as a handoff document for future work. The Python source is authoritative when this document and older prose differ.

## 1. Product purpose

The application is a curses TUI for browsing, searching, editing, sorting, and resuming coding-agent sessions. It reads a human-editable session log, optionally discovers recent sessions from installed harnesses, and launches or focuses the selected session.

The implementation is a small Python package in `session_manager/`. There is no build system or external application framework. The test suite is `tests/test_session_manager.py`.

## 2. Running the application

From the repository directory:

```sh
./aisema
```

CLI options:

```text
--file PATH       Session log path. Overrides the configured sessions_file.
--config PATH     Configuration path. Default: ~/.config/aisema/config
COUNT|ALL         Optional discovery selector. Omit it to use only the configured session log.
```

`COUNT` must be a positive integer. `all` is case-insensitive and discovers all sessions available through installed supported harnesses. A numeric selector discovers the globally newest `COUNT` sessions across the selected installed harnesses.

The CLI loads the session document first, optionally adds discovered entries, loads configuration, and starts curses through `run_tui()`.

## 3. Repository architecture

```mermaid
flowchart TD
    CLI[__main__.py] --> STORAGE[storage.py]
    CLI --> DISC[discovery.py]
    CLI --> CONFIG[config.py]
    CLI --> TUI[tui.py]
    STORAGE --> MODEL[model.py]
    DISC --> MODEL
    TUI --> MODEL
    TUI --> CONFIG_EDITOR[config_editor.py]
    TUI --> PROCESS[process.py]
    TUI --> TMUX[tmux.py]
    CONFIG_EDITOR --> CONFIG
```

### `session_manager/__main__.py`

- Defines CLI defaults and argument parsing.
- Loads configuration before selecting the session document path.
- Calls `load_document()`.
- Calls `available_harnesses()` and, when a selector is supplied, `discover_recent()`.
- Calls `run_tui()`.

### `session_manager/model.py`

Defines the session data model and parsing rules:

- `SessionEntry` represents one non-empty session line or one ephemeral discovered/temporary session.
- `parse_line()` recognizes supported resume-command shapes and preserves unrecognized non-empty lines as visible but non-launchable entries.
- Marker handling, session IDs, runner names, short command display, serialization, and last-used date formatting live here.

### `session_manager/storage.py`

Defines `SessionDocument`:

- Preserves parsed entries and blank lines in their original file order.
- Keeps discovered and new-session entries in an `ephemeral` list until explicitly persisted.
- Persists edits atomically through a temporary file, `fsync()`, and `os.replace()`.

### `session_manager/config.py`

Defines configuration defaults, loading, validation of selected values, and typed boolean properties. Missing settings are appended to the config file as commented defaults.

### `session_manager/config_editor.py`

Provides the in-TUI configuration editor. It understands known keys from `DEFAULTS`, preserves unrelated raw lines, keeps configuration keywords fixed, and saves atomically.

### `session_manager/discovery.py`

Provides:

- Installed-harness detection.
- Per-harness session discovery adapters.
- Timestamp normalization to nanoseconds.
- Description extraction from latest user prompts or titles.
- Preservation of all extracted user queries for the `M` inspection popup.
- Last-used timestamp refresh for existing entries.

### `session_manager/process.py`

Finds active sessions by scanning `/proc`, matching session ID and runner, and falling back to OMP terminal-session metadata when the session ID is not present in the process command line.

### `session_manager/tmux.py`

Detects tmux, enumerates panes, maps a process to a pane through the process-parent tree, focuses an existing pane, and launches a new detached tmux window. New-window launches retain the created pane/window identity so a temporary new session can be focused again without launching a duplicate window.

### `session_manager/tui.py`

Owns the curses application:

- Main list rendering and layout.
- Search, edit, marker toggles, sorting, configuration, help, and new-session flows.
- Active-state refresh.
- Launch/focus behavior.
- Live last-used timestamp refresh and formatting.

## 4. Session file format

The default session file is `~/.config/session-manager/sessions.txt`. The `sessions_file` configuration key overrides this path, and `--file` takes precedence over the configuration.

### 4.1 Lines

- Blank lines have no session meaning and are preserved on save.
- Each non-empty line is user-owned text.
- Recognized entries begin with a complete resume command followed by one or more spaces and a description.
- The exact command and separator are preserved unless the entry is edited or its marker is changed.
- Unrecognized non-empty lines remain visible and searchable, but `parsed=False` prevents editing marker state and launching.

Recognized command patterns:

```text
<runner> [--flag ...] --resume <session-id> [--flag ...]
<runner> [--flag ...] --session <session-id> [--flag ...]
goose [--flag ...] session resume --name <session-id> [--flag ...]
```

The parser specifically supports the command shapes represented by `_ENTRY_RE`; the launch command is otherwise preserved exactly for recognized entries.

### 4.2 Entry fields

`SessionEntry` fields:

| Field | Meaning |
|---|---|
| `raw` | Original serialized line. Used while the entry is clean. |
| `command` | Parsed or preserved launch command. |
| `separator` | Spaces between command and description. |
| `description` | User-visible description, including marker suffixes. |
| `line_number` | Original/pseudo line position used for original ordering. |
| `parsed` | Whether the command matched a supported resume shape. |
| `source` | `file`, `discovered`, or `temporary`. |
| `active` | Whether a matching running process was found. |
| `last_used_ns` | Last update time as Unix nanoseconds; `0` means unknown. |
| `_dirty` | Internal flag indicating that serialization must be rebuilt. |

Computed properties:

- `session_id`: ID extracted from `--resume`, `--session`, or `--name`.
- `runner`: basename of the first command token.
- `short_session_id`: final hyphen-separated component of the session ID.
- `short_command`: runner plus short ID, or the full command if no ID exists.
- `done`: description ends with ` Done.`, ignoring a trailing ` Throwaway.` marker for the done check.
- `throwaway`: description ends with ` Throwaway.`.
- `discovered`: `source == "discovered"`.
- `temporary`: `source == "temporary"`.

### 4.3 Markers

Markers are literal description suffixes:

```text
 Done.
 Throwaway.
```

- Done entries remain visible after unfinished entries and are never deleted.
- Throwaway entries are hidden by default.
- Both markers are supported, in this order: `... Done. Throwaway.`.
- `toggle_done()` preserves the throwaway suffix when present.
- `toggle_throwaway()` preserves the done suffix when present.
- Marker changes mark the entry dirty and are saved through the normal TUI save path.

## 5. Persistence and ephemeral entries

`SessionDocument.lines` contains persisted `SessionEntry` and `BlankLine` objects. `SessionDocument.ephemeral` contains discovered and newly created temporary entries.

`document.entries` returns persisted entries followed by ephemeral entries.

### Discovered entries

- Added by `add_discovered()`.
- Deduplicated against existing entries by `(runner, session_id)`.
- Assigned pseudo line numbers after the persisted file lines.
- Start with `source="discovered"`.
- Are not written merely because they were discovered.
- Become persisted when the user edits the description or changes a done/throwaway marker; materialization changes `source` to `file` and clears `N`.

### New-session entries

- Created by the `N` shortcut or `:new session`.
- Start with `source="temporary"`, command equal to the selected harness name, and description `New <runner> session`. If both `agent` and `cursor-agent` are installed, new-session selection excludes `cursor-agent` in favor of `agent`; existing stored commands are not rewritten.
- Launch alone does not write the entry to the session file.
- The TUI records pre-launch known IDs for that runner, polls discovery after launch and during the normal refresh loop, and refreshes discovery when focusing the tracked tmux pane. It replaces the temporary command with the harness-specific resume command when the new ID appears.
- The captured ID remains ephemeral and visible with the `N` marker until the user edits the description.
- Editing the description materializes the entry into `sessions.txt`, preserving the captured resume command and updated timestamp. Done/throwaway changes alone do not materialize a new-session entry.

### Atomic saves

`SessionDocument.save()` serializes only `lines`, not remaining ephemeral entries. It preserves blank lines and final-newline state, creates the parent directory, writes a sibling temporary file, flushes and fsyncs it, then replaces the target path atomically.

## 6. Discovery architecture

Supported harness names and discovery order:

```text
copilot, agent, cursor-agent, opencode, omp, goose
```

Only commands found by `shutil.which()` are considered installed for normal discovery and new-session selection.

### Sources

- `copilot`: `~/.copilot/history-session-state` and `~/.copilot/sessions`, JSON files.
- `agent`: `~/.agent/sessions`, `~/.agent/history`, and `~/.config/agent/sessions`, recursive JSON/JSONL files.
- `cursor-agent`: `~/.cursor/projects/*/agent-transcripts/*/*.jsonl`.
- `opencode`: first usable SQLite database at `~/.local/share/opencode/opencode.db` or `~/.config/opencode/opencode.db`; reads root sessions from `session`.
- `omp`: `$PI_CODING_AGENT_DIR/sessions`, or `~/.omp/agent/sessions`; recursively reads JSONL files.
- `goose`: `~/.local/share/goose/sessions` and `~/.config/goose/sessions`, including `sessions.db` and JSONL files.

### Normalization

- Session metadata is converted to `DiscoveredSession` with runner, ID, description, resume command, source path, and `updated_ns`.
- Numeric timestamps are interpreted heuristically as seconds, milliseconds, microseconds, or nanoseconds based on magnitude.
- ISO timestamps are accepted, including trailing `Z`.
- Descriptions prefer the latest user prompt; title/name/description metadata is used as fallback.
- Empty descriptions fall back to `<runner> session` where necessary.
- Resume command generation is special-cased for OpenCode and Goose:

```text
opencode --session <id>
goose session resume --name <id>
<other-runner> --resume <id>
```

`discover_recent()` sorts all discovered metadata newest-first by `(updated_ns, runner, session_id)` and applies the requested global limit. `all` applies no limit.

`refresh_last_used(entries)` discovers all available harnesses, keeps the newest timestamp per `(runner, session_id)`, and writes that timestamp into matching `SessionEntry.last_used_ns`; unmatched entries receive `0`.

## 7. Sorting specification

Sorting is performed by `_sort_entries(entries, mode, descending, first_mode)` in `tui.py`. The application stores the first-layer mode separately from the second-layer mode.

The first layer groups rows before applying the second layer:

- `status` (default): active non-throwaway rows, inactive unfinished rows, inactive done rows, then throwaway rows.
- `none`: one group containing all rows.
- `date`: one group per local calendar date, newest date first; unknown dates are last.
- `command`: one group per runner (`omp`, `goose`, etc.), runner names ascending.

The second layer sorts only inside each first-layer group:

- `date` (default): raw `last_used_ns`, newest first; unknown timestamps (`0`) are last.
- `none`: preserve the current order.
- `original`: `line_number`.
- `id`: case-folded session ID.
- `command`: case-folded full command.
- `description`: case-folded description.
- `active`: the existing active boolean ordering.

The `descending` flag reverses non-date second-layer keys. Date ordering remains newest-first. The old `status` second-layer value remains accepted as a compatibility alias for `date`.

Pressing `S` opens the second-layer picker. Pressing `S` again while the picker is open switches to the first-layer options; pressing it again switches back. Enter applies the selected layer and option. `q` or Escape cancels. Direct `:sort MODE [desc|descending]` continues to select the second-layer mode without changing the first layer.

After sorting, `SessionApp.rebuild()` applies visibility filtering and search filtering. Hidden throwaway rows are removed unless their object ID is in `throwaway_visible`. Search matches case-insensitive description, command, or session ID.

## 8. TUI state and rendering

`SessionApp` initializes with:

- `sort_mode="status"`.
- `sort_descending=False`.
- Empty query.
- No selected row and no scroll offset.
- `show_last_used_date` from configuration.
- Color pairs configured from color settings.

When date display is enabled at startup or after config reload, `refresh_last_used()` runs before rebuilding the list.

The main list layout is:

```text
[4 status slots][command column][optional date column] [description]
```

Status slots are fixed in this order:

```text
A D X N
```

`N` means the entry is not currently represented by a line in `sessions.txt`: the implementation sets it when `entry.source != "file"`. This includes discovered entries not yet materialized and newly created sessions with `source="temporary"`. A newly created session can have its discovered resume command and ID in memory while it still has `N`; editing its description materializes it and clears `N`. Missing states are spaces. The command column is either the full command or `short_command`. If enabled, the date column uses `SessionEntry.last_used_text(config.get("date_format"))`. The date is local time and is omitted for unknown timestamps.

The default date format is `%d.%m.%Y`, displayed as `dd.mm.yyyy`. Any Python `strftime` format string is accepted through the `date_format` setting. Invalid/unsupported timestamp conversion returns an empty display string; the configured format is otherwise passed to `datetime.strftime()`.

The TUI refreshes active process state and pending new-session discovery approximately once per second while drawing. Focusing a tracked temporary tmux pane also triggers an immediate discovery refresh. Rebuilding preserves the selected object where possible and keeps the selected row in the visible scroll range.

## 9. Configuration specification

Default file: `~/.config/aisema/config`.

The loader:

- Creates the parent directory and a commented config when absent.
- Accepts `key=value` lines.
- Ignores blank lines, unknown keys, and malformed lines.
- Treats `#` and `;` entries as comments.
- Appends missing known defaults as comments.
- Merges active values over `DEFAULTS`.
Normalizes invalid `display_command`, `show_throwaway`, and `show_done` values to their defaults; an empty `sessions_file` uses its default.

Current defaults:

```ini
sessions_file=~/.config/session-manager/sessions.txt
display_command=full
show_throwaway=false
show_done=true
date_format=%d.%m.%Y
show_last_used_date=false
color_runner=pink
color_hash=light_gray
color_command=blue
color_description=white
color_done=blue
color_throwaway=dark_gray
color_selected=cyan
color_prompt=yellow
color_error=red
```

Properties:

- `display_command=short` enables short command display.
- `display_command=none` hides the command column; descriptions begin after the status slots and optional date column.
- Boolean properties recognize `1`, `true`, `yes`, and `on` as true; corresponding false forms are accepted for the loader/UI behavior.
- `show_throwaway=false` hides throwaway rows unless temporarily overridden by a local toggle.
- `show_done=false` hides rows whose description ends with ` Done.`.
- `sessions_file` selects the session log when `--file` is not supplied.

The config editor lists every known default key. It supports:

- Up/Down/Page Up/Page Down/Home/End navigation.
- Space toggling for recognized boolean values.
- Pickers for `display_command` and `color_*` values.
- Text editing for other values, including `date_format`.
- `;` or `#` to activate/comment the selected setting.
- Immediate atomic save and live reload after each change.
- When enough space exists, live examples include throwaway, active (`A`), selected, unsaved (`N`), done, and normal rows; all examples that fit are rendered, and rows are omitted when the terminal is too short.
- `q`, `Q`, or Escape to leave config mode.

## 10. Commands and keyboard controls

Shortcuts are case-insensitive unless noted:

| Key | Action |
|---|---|
| Up/Down | Move selection. |
| Page Up/Page Down | Move one screen. |
| Home/End | Jump to first/last row. |
| `F` | Prompt for `:find`. |
| `Ctrl-W` | Prompt for `:find`. |
| `E` | Edit selected description. |
| `D` | Toggle done marker. |
| `S` | Choose a first- or second-layer sort mode from a popup; press `S` in the popup to switch layers. |
| `C` | Open configuration editor. |
| `X` / `T` | Toggle throwaway marker. |
| `M` | Show full description and recorded user queries; Up/Down/Page Up/Page Down/Home/End navigate queries, and `q`/Escape closes the popup. |
| `H` | Show help. |
| Enter | Launch or focus selected session. |
| `Q` | Quit. |
| Escape | Clear search in the main view; closes help/config contexts. |

Colon commands:

```text
:find TEXT       Set case-insensitive search text; empty clears search.
:edit            Edit selected description.
:done            Toggle selected done marker.
:undone          Remove selected done marker.
:sort MODE [desc|descending]
                 MODE: status, original, id, command, description, active.
:new session     Choose an installed harness and start a temporary session.
:date            Toggle date display.
:date on         Enable date display for the current TUI session.
:date off        Disable date display for the current TUI session.
:config          Enter config editor.
:help            Show help.
```

`:date` changes live display state but does not write `show_last_used_date` to configuration. Configuration changes must be made through the config file/editor.

## 11. Launch and active-session behavior

When Enter is pressed on an unrecognized line, launch is rejected.

For recognized entries:

1. A newly created temporary entry with a tracked tmux pane refreshes pending ID discovery, then focuses that original pane. It does not launch another tmux window.
2. `process.find_process()` searches for a process containing the session ID and the matching runner basename.
3. If found and the process belongs to a tmux pane, the manager focuses that window/pane and switches the tmux client.
4. If found outside tmux, the manager reports that the session is already active and does not launch another copy.
5. If not active and currently inside tmux, `tmux.launch()` opens a detached new window named from the first space-delimited token of the session description, truncated to 16 characters. An empty description falls back to `session`. It runs the exact stored command.
6. If not active outside tmux, curses is suspended and the exact stored command is run through `/bin/sh`; curses is restored afterward.

A process is also considered active through OMP terminal-session metadata: marker files under `$PI_CODING_AGENT_DIR/terminal-sessions` or `~/.omp/agent/terminal-sessions` map `pts-*` files to session JSONL names, then the process on that tty is matched by runner.

## 12. Tests and verification

Run the full test suite:

```sh
python3 -m unittest discover -s tests -v
```

Run compilation checks:

```sh
python3 -m compileall -q session_manager tests
```

The current test suite covers:

- Parsing and serialization-related marker behavior.
- Fixed status marker columns.
- New temporary session creation and harness ordering.
- Word navigation.
- Color aliases.
- Process and terminal-session detection.
- Blank-line/separator-preserving persistence.
- Discovered-entry materialization.
- Status grouping and newest-first last-used sorting.
- Throwaway visibility behavior.
- Configuration defaults, comments, custom date format, and editor behavior.
- Live date toggling.
- Harness availability lookup.
- Cross-harness and OMP discovery, prompt extraction, and timestamp ordering.
- Supported command shapes.
- CLI behavior without a discovery selector.

A terminal smoke test is recommended for changes affecting curses rendering or launch flow. Date rendering can be exercised with a temporary config containing:

```ini
show_last_used_date=true
date_format=%d.%m.%Y
```

## 13. Continuation guidance and invariants

Preserve these invariants when extending the code:

1. Do not reorder or rewrite user-owned session lines merely to display a sorted view. Sorting is in-memory only.
2. Preserve blank lines and the file’s final-newline state on save.
3. Persist discovered entries only when the user changes them; discovery itself remains ephemeral.
4. Deduplicate discovered sessions by `(runner, session_id)`.
5. Keep done and throwaway suffixes composable and preserve the suffix not being toggled.
6. Keep malformed non-empty lines visible but non-launchable.
7. Use the exact stored command for launching; do not reconstruct recognized file commands.
8. Keep throwaway rows after ordinary rows and hidden by default.
9. Keep last-used sorting based on raw `last_used_ns`, not formatted date strings. Formatting can lose time precision and can be customized by the user.
10. Keep the date secondary layer newest-first even when a primary sort mode is descending.
11. When adding a config key, add it to `DEFAULTS`; the loader and config editor derive their known-key lists from it.
12. When changing exported model or TUI behavior, update all call sites and add a consumer-visible test.
13. After source changes, run unit tests, compile checks, and a real TUI smoke scenario for rendering/interaction changes.

## 14. Known documentation/maintenance notes

- `session_format.md` is the user-facing format document and overlaps with this handoff specification.
- The implementation uses `X` for the throwaway shortcut; the user-facing format documentation matches the current TUI key handler.
- `session_manager/__init__.py` reports version `0.1.0`; there is no packaging metadata or release automation in this repository.
- Generated `__pycache__` files are not part of the source architecture and should not be edited or documented as source files.
