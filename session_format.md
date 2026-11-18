# Session Format

`../sessions.txt` is a human-readable session resume list. Each non-empty line is one session entry and stores the command used to restart the session followed by the user’s free-form description.

## Entry format

A non-empty line MUST begin with the complete session-resume command. The command is followed by the command/description separator used by the log: one or more literal spaces. The command portion MAY target a built-in or installed plugin runner and MUST be preserved exactly as the restart command. Unknown executable names also retain recognition of the neutral `--resume`/`--session` option forms.

All remaining text on the line is the user-owned description. The description is display text, not a set of structured fields. It MAY state an objective, current status, or future work; no other semantics are imposed. User wording and entry ordering MUST be preserved.

There is no universal shell parser or escaping convention in this format. A registered runner defines its command grammar and boundary, including any custom quoting rules; the remaining text after the separator is the description.

## Completion

A line whose description ends with the literal ` Done.` is permanently completed by the user. The marker is part of the stored line. Done entries MUST NOT be deleted; they remain as historical reference in the done log. A description that does not end with that exact marker remains resumable/current according to the user’s judgment.

## Throwaway entries

A line whose description ends with the literal ` Throwaway.` is a throwaway entry. Throwaway entries are hidden by default. Set `show_throwaway=true` in the configuration to display them. When both markers are present, the done marker precedes the throwaway marker, for example `Temporary Done. Throwaway.`.

Throwaway entries are kept after ordinary unfinished and done entries when displayed. They remain in the session file and are not deleted. The `X` shortcut saves the marker immediately for persisted entries, but keeps a newly created temporary row ephemeral; throwaway rows use dark gray text.


## Examples

```text
omp --resume 01a0f35d-3bdf-729e-85df-e2c96a772f4d Kuitti OCR to excel
omp --resume 01a10e27-2fb9-74bc-bb24-be1c136fe9dc Current status: OCR fields still need review
omp --resume 01a1183a-8811-72c7-83ca-7036cd9855b1 Future work: export the reviewed rows
omp --resume 01a2b3c4-5d6e-7f80-9012-34567890abcd Archive the completed import Done.
```

Blank lines, if present, have no session meaning. Entries are not otherwise reclassified by this specification.

## Aisema behavior

The Aisema TUI reads the configured session file, or the path from `sessions_file`/`--file`, displays every non-empty entry, and keeps completed entries visible after entries without the terminal done marker. The first column has fixed status slots in order: `A` active, `D` done, `X` throwaway, and `N` not yet present in `sessions.txt`; missing statuses remain blank so each letter stays in the same character column. For example, a row with all four statuses starts `ADXN omp 32094u320`. The command column shows the stored command by default; the configuration can instead show the runner and final hyphen-separated session-ID component, such as `omp 4785cd455157`. Within each status group, entries are ordered by last-used time with the newest first; the selected sort mode remains the primary key when it …

Built-ins recognize `<runner> [--flag ...] --resume <id> [--flag ...]`, `<runner> [--flag ...] --resume=<id> [--flag ...]`, `<runner> [--flag ...] --session <id> [--flag ...]`, `<runner> [--flag ...] --session=<id> [--flag ...]`, `goose [--flag ...] session resume --name <id> [--flag ...]`, and `codex resume <session-id>` command shapes. For the flag-based shapes, supported long-option tokens may precede or follow the selector. Registered plugins may define additional command shapes; their parser must return an exact command prefix with a nonempty ID, followed by the literal description separator. A registered runner's rejection is never overridden by generic parsing. Other non-empty lines remain visible and searchable but unlaunchable.

With no positional selector, startup uses the configured session file and adds active discoveries. An optional positive integer discovers that many globally newest sessions across installed runners; built-ins are `copilot`, `agent`, `cursor-agent`, `opencode`, `omp`, `goose`, and `codex`, and external plugins participate through the same registry. `all` is case-insensitive and discovers every available session. Each runner's availability check (executable lookup by default) gates automatic discovery and the new-session picker, not saved-command parsing or explicit runner discovery. Discovered entries are temporary until changed. A newly created `N` session uses its runner's exact new-session command until its generated ID is discovered, then adopts the resume command in memory and remains ephemeral. Picker hiding is declared by runners; Cursor's declaration omits `cursor-agent` when `agent` is installed, without rewriting stored commands.

For discovered sessions, the default description is the latest user prompt from the session record when available; otherwise the harness title or session name is used.
For OMP sessions, recap records from the agent history database are combined with user queries and shown by the `M` popup in recorded timestamp order. The opening page shows the latest recap below the description when available.

Codex discovery reads `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`
(default `~/.codex/sessions/...`) and extracts the UUID thread ID from
`session_meta`, falling back to the validated filename. The latest normalized
user prompt becomes the description, and all recognized user prompts appear
as timestamp-ordered query inspection items. Malformed, partial, unknown, and
unreadable records are ignored; the file mtime supplies discovery and invalid
record timestamps. `.jsonl.zst` files are identified by filename and use the
generic `Codex session` description because Aisema has no zstd decoder.

Runner plugins are direct `.py` modules in `~/.config/aisema/runners/`, loaded on
startup regardless of `--config`. Canonical runner identity may differ from the
executable basename; document deduplication uses canonical `(runner, session_id)`.
Commands and separators remain exact when descriptions or markers are edited.
Removing a custom-syntax plugin preserves its saved text as visible, unparsed,
launch-blocked lines. Executable unavailability alone does not remove its parser
or inspection support. Plugins are trusted, unsandboxed user code; see
[`add_runner.md`](add_runner.md) and the illustrative
[`examples/runner_template.py`](examples/runner_template.py) for the public
contract and verified-facts onboarding.


The `show_throwaway` setting controls whether throwaway entries are visible. Hidden throwaway entries are also excluded from searches until enabled. `show_done=false` filters entries whose description ends with ` Done.` from the session list; the default is `true`.
When `show_last_used_date=true`, the list inserts the last-used timestamp (`%d.%m.%Y` by default, local time) between the command column and description. `date_format` accepts a Python `strftime` format string, and `:date` toggles this display live; `:date on` and `:date off` select an explicit state without changing the saved configuration. `session_list_update_interval` controls active-session and pending-session list updates in seconds; it accepts values from `0.001` through `600` and defaults to `2`.

The configuration file is `~/.config/aisema/config`. On first run it is created with every supported setting commented out at its default value. Missing settings are appended as commented defaults without replacing existing settings. Current settings are:

```ini
# display_command=full
# show_throwaway=false
# show_done=true
# date_format=%d.%m.%Y
# show_last_used_date=false
# session_list_update_interval=2
# color_letters=yellow
# color_runner=pink
# color_runner_sessionid=light_gray
# color_runner_parameters=blue
# color_description=white
# color_description_done=green
# color_description_throwaway=blue
# color_selected=cyan
# color_statusline=bright_cyan
# color_error=red
```

With `display_command=short`, commands show the runner followed by only the final hyphen-separated session-ID component. With `display_command=none`, the command column is hidden and descriptions start immediately after the status slots (plus the optional date column). The runner is pink by default and that short ID component is light gray. Sessions registered in OMP terminal-session metadata are marked active even when the running `omp` process does not include the session ID in its command line.

The main window accepts case-insensitive shortcuts `F` find, `E` edit, `D` toggle done, `S` choose a first- or second-layer sort mode from a popup, `C` open config, `X`/`T` toggle throwaway, `M` inspect the full description plus ordered recaps and user queries, `R` refresh discovery, `H` show help, `N` start a new session, and `Q` quit. `R` requests five sessions beyond the amount currently loaded in the session list. In the `M` popup, the opening page shows the latest recap below the description when one exists; Up/Down move one recap or query, Page Up/Down skip five recap/query items, `M` moves down one item, and `M` on the last item closes the popup; Home/End jump to the first/last item; `q` or Escape also closes the popup. Recaps and user queries are ordered by their recorded timestamps.

`:config` opens the configuration editor. Up/Down select a setting, Space toggles recognized boolean values or opens a picker for non-toggleable options, Enter opens the same picker or edits only the value (the keyword cannot be changed); changing a value also activates the line if it was commented. `;` or `#` toggles the selected line between active and commented. Color options use a palette popup. Changes are saved immediately. Live examples are rendered below the settings, including throwaway, active, selected, unsaved, done, and normal rows when the terminal is tall enough. `display_command`, `date_format`, and `show_last_used_date` affect the example rendering; color settings affect their styles. `q` exits the configuration editor.

When Enter is pressed for an inactive persisted session inside tmux, the exact stored command is launched in a new tmux window. A newly created temporary session is different: Enter focuses the original tmux pane created for that session and does not launch another window. An active session in a tmux pane is focused instead; an active session outside tmux is not launched again. Outside tmux, an inactive command runs in the foreground and the TUI resumes after it exits.

Blank lines have no session meaning and are preserved when the file is rewritten. Changes are saved atomically.
