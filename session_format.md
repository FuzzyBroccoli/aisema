# Session Format

`../sessions.txt` is a human-readable session resume list. Each non-empty line is one session entry and stores the command used to restart the session followed by the user’s free-form description.

## Entry format

A non-empty line MUST begin with the complete session-resume command. The command is followed by the command/description separator used by the log: one or more spaces. The command portion MAY target any supported session runner, including `agent`, `omp`, or another applicable command, and MUST be preserved exactly as the restart command.

All remaining text on the line is the user-owned description. The description is display text, not a set of structured fields. It MAY state an objective, current status, or future work; no other semantics are imposed. User wording and entry ordering MUST be preserved.

No machine parser or escape syntax is defined by this format. The command and description are recorded in the existing line shape.

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

The manager recognizes `<runner> [--flag ...] --resume <id> [--flag ...]`, `<runner> [--flag ...] --resume=<id> [--flag ...]`, `<runner> [--flag ...] --session <id> [--flag ...]`, and `goose [--flag ...] session resume --name <id> [--flag ...]` command shapes. Additional flag tokens beginning with `--` may appear before the resume selector, after its session ID, or both. Other non-empty lines remain visible and searchable, but are not launchable because their command boundary cannot be identified.

With no positional selector, startup uses the configured session file. An optional positional integer discovers that many newest sessions across installed `copilot`, `agent`, `cursor-agent`, `opencode`, `omp`, and `goose` harnesses. `all` is case-insensitive and discovers every available session. Discovery uses the fast executable lookup for installed-harness detection, then merges all selected harnesses and sorts them newest-first by update time. Discovered entries are temporary until changed. A newly created `N` session is tracked until its newly generated session ID is discovered; the command is then replaced in memory with the harness-specific resume command and remains ephemeral. The new-session picker omits `cursor-agent` when the shorter `agent` launcher is also installed; existing stored commands are unchanged. Discovery runs after launch, during the normal refresh loop, and when focusing …

For discovered sessions, the default description is the latest user prompt from the session record when available; otherwise the harness title or session name is used.


The `show_throwaway` setting controls whether throwaway entries are visible. Hidden throwaway entries are also excluded from searches until enabled. `show_done=false` filters entries whose description ends with ` Done.` from the session list; the default is `true`.
When `show_last_used_date=true`, the list inserts the last-used timestamp (`%d.%m.%Y` by default, local time) between the command column and description. `date_format` accepts a Python `strftime` format string, and `:date` toggles this display live; `:date on` and `:date off` select an explicit state without changing the saved configuration.

The configuration file is `~/.config/aisema/config`. On first run it is created with every supported setting commented out at its default value. Missing settings are appended as commented defaults without replacing existing settings. Current settings are:

```ini
# display_command=full
# show_throwaway=false
# show_done=true
# date_format=%d.%m.%Y
# show_last_used_date=false
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

The main window accepts case-insensitive shortcuts `F` find, `E` edit, `D` toggle done, `S` choose a first- or second-layer sort mode from a popup, `C` open config, `X`/`T` toggle throwaway, `M` inspect the full description and recorded user queries, `R` refresh discovery, `H` show help, `N` start a new session, and `Q` quit. `R` starts discovery at 5 sessions; consecutive `R` presses increase the requested count by 5, while another key resets the consecutive count. In the `M` popup, the first Up shows the latest query and further Up keys move older; Down starts at the first query and moves newer; Page Up/Down move by a screen; Home/End jump to the first/last query; `q` or Escape closes the popup. Press `S` again while the sort popup is open to switch layers. Commands starting with `:` continue to work as before; `:new session` opens the same harness picker and `:date` toggles last-used dates. `Ctrl-W` starts `:find`.

`:config` opens the configuration editor. Up/Down select a setting, Space toggles recognized boolean values or opens a picker for non-toggleable options, Enter opens the same picker or edits only the value (the keyword cannot be changed), and `;` or `#` toggles the selected line between active and commented. Color options use a palette popup. Changes are saved immediately. Live examples are rendered below the settings, including throwaway, active, selected, unsaved, done, and normal rows when the terminal is tall enough. `display_command`, `date_format`, and `show_last_used_date` affect the example rendering; color settings affect their styles. `q` exits the configuration editor.

When Enter is pressed for an inactive persisted session inside tmux, the exact stored command is launched in a new tmux window. A newly created temporary session is different: Enter focuses the original tmux pane created for that session and does not launch another window. An active session in a tmux pane is focused instead; an active session outside tmux is not launched again. Outside tmux, an inactive command runs in the foreground and the TUI resumes after it exits.

Blank lines have no session meaning and are preserved when the file is rewritten. Changes are saved atomically.
