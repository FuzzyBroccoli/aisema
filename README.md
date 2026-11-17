# Aisema

A small curses TUI for browsing, searching, sorting, editing, and resuming coding-agent sessions.

`Aisema` keeps a human-readable session log, optionally discovers recent sessions from installed agent harnesses, and launches or focuses the selected session. It is designed for terminal and tmux workflows.

## Features

- Browse and search a human-editable `sessions.txt` file.
- Resume supported sessions with `Enter`.
- Focus already-running sessions instead of launching duplicates.
- Create a temporary new session with `N` or `:new session`.
- Discover recent sessions from installed harnesses:
  - `copilot`
  - `agent`
  - `cursor-agent`
  - `opencode`
  - `omp`
  - `goose`
- Detect active OMP terminal sessions through terminal-session metadata.
- Sort with independent primary and secondary sort layers.
- Mark sessions done or throwaway without deleting history.
- Show full commands, short runner/ID commands, or hide the command column.
- Preserve blank lines, ordering, descriptions, and final-newline state when saving.
- Use atomic session-file writes.

## Requirements

- Python 3.10 or newer.
- A Unix-like terminal with curses support.
- Optional: tmux for detached launches and pane/window focusing.
- Optional: supported agent harness executables for discovery and new-session creation.

The project uses Python’s standard library and has no repository dependency file or package installer configuration.

## Run from a checkout

```sh
./aisema
```

The default configuration path is:

- Configuration: `~/.config/aisema/config`

Use explicit paths when running from another directory:

```sh
./aisema \
  --file /path/to/sessions.txt \
  --config ~/.config/aisema/config
```

The session file is created if it does not exist. Its path can be changed with `sessions_file` in the configuration or overridden with `--file`. The configuration file is created with commented defaults on first run.

## Discover recent sessions

Without a positional selector, startup loads only the session file. Pass a positive count to discover that many newest sessions across installed supported harnesses, or use `all` to discover every available session:

```sh
./aisema 20
./aisema all
```

Discovery entries are temporary until changed. Editing their description or changing a marker materializes them into `sessions.txt`.

## Session file format

Each non-empty line contains a complete resume command, one or more spaces, and a free-form description:

```text
omp --resume 01a0f35d-3bdf-729e-85df-e2c96a772f4d Investigate the import failure
opencode --session ses_1234 Review the API design
```

Each normal entry must use one of the recognized command shapes:

```text
<runner> [--flags] --resume <id> [--flags] <description>
<runner> [--flags] --resume=<id> [--flags] <description>
<runner> [--flags] --session <id> [--flags] <description>
goose [--flags] session resume --name <id> [--flags] <description>
```

Descriptions ending in ` Done.` remain visible as completed history. Descriptions ending in ` Throwaway.` are hidden by default. Both markers may be combined as `Done. Throwaway.`.

See [`session_format.md`](session_format.md) for the complete format and rendering specification.

## New sessions and tmux

Press `N` or run `:new session` to choose an installed harness and create a temporary session.

The manager records the existing IDs for that harness, launches the new session, and polls harness discovery for the newly generated ID. When the ID becomes available, the in-memory command changes to the harness-specific resume command and the hash appears in the session list with the `N` marker.
When both `agent` and `cursor-agent` are installed, the new-session picker offers the shorter `agent` launcher. Existing stored entries keep their exact stored runner command.

New-session behavior:

- Launching alone does not write the temporary entry to `sessions.txt`.
- Editing the description saves the captured resume command and ID.
- Leaving the description untouched keeps the entry ephemeral.
- If discovery is delayed, description editing waits briefly for the ID and will not save a bare runner command.
- In tmux, `Enter` focuses the original pane created for a new session instead of opening another window.
- For ordinary inactive persisted entries, `Enter` launches the exact stored command in a new tmux window.

Tmux focus uses pane/window identifiers. Those identifiers are separate from the agent harness session ID; the harness ID comes from the harness’s session metadata.

## Keyboard controls

| Key | Action |
|---|---|
| `Up` / `Down` | Move selection. |
| `Page Up` / `Page Down` | Move one screen. |
| `Home` / `End` | Jump to the first or last row. |
| `F` | Prompt for `:find`. |
| `Ctrl-W` | Prompt for `:find`. |
| `E` | Edit the selected description. |
| `D` | Toggle the done marker. |
| `S` | Choose a sort mode; press `S` in the popup to switch sort layers. |
| `C` | Open the configuration editor. |
| `X` / `T` | Toggle the throwaway marker. |
| `M` | Show the full description and browse recorded user queries. Up first shows the latest query, then older queries; Down starts at the first query, then moves newer. Page Up/Down move by a screen. Press `M` again to page down; at the last query, `M` closes the popup. Home/End jump to the first/last query; `q` or Escape also closes the popup. |
| `R` | Refresh discovery by requesting five sessions beyond the amount currently loaded in the session list. |
| `H` | Show help. |
| `Enter` | Launch or focus the selected session. |
| `Q` | Quit. |
| `Escape` | Clear search or close a modal context. |

## Colon commands

```text
:find TEXT       Search descriptions, IDs, and commands.
:edit            Edit the selected description.
:done            Toggle the done marker.
:undone          Remove the done marker.
:sort MODE       Sort by a mode, optionally followed by desc.
:new session     Choose a harness and start a temporary session.
:date            Toggle last-used date display.
:date on         Enable last-used dates for this TUI session.
:date off        Disable last-used dates for this TUI session.
:config          Open the configuration editor.
:help            Show help.
```

Sort modes include `status`, `date`, `none`, `original`, `id`, `command`, `description`, and `active`, subject to the selected sort layer.

## Configuration

Configuration is stored at `~/.config/aisema/config` by default. Supported settings include:

```ini
display_command=full
show_throwaway=false
show_done=true
date_format=%d.%m.%Y
show_last_used_date=false
session_list_update_interval=2
color_letters=yellow
color_runner=pink
color_runner_sessionid=light_gray
color_runner_parameters=blue
color_description=white
color_description_done=green
color_description_throwaway=blue
color_selected=cyan
color_statusline=bright_cyan
color_error=red
```

`display_command` accepts:

- `full`: display the complete resume command.
- `short`: display the runner and final hyphen-separated session-ID component.
- `none`: hide the command column.

Set `show_done=false` to remove entries whose description ends with ` Done.` from the session list. The default is `true`. `session_list_update_interval` controls active-session and pending-session list updates in seconds; it accepts values from `0.001` through `600` and defaults to `2`.

Use `C` or `:config` to edit settings interactively. Configuration changes are written atomically and reloaded live.

## Development

Run the test suite:

```sh
python3 -m unittest discover -s tests -v
```

Run the compile check:

```sh
python3 -m compileall -q session_manager tests
```

The implementation is in [`session_manager/`](session_manager/) and tests are in [`tests/test_session_manager.py`](tests/test_session_manager.py). The detailed architecture and behavior handoff is in [`session-manager-specs.md`](session-manager-specs.md).

## Version

Current package version: `0.1.0`.

## License

The Aisema program files in this repository are licensed under the GNU General Public License, version 3 or later. See [`LICENSE`](LICENSE).

This license applies to the `session_manager/` program and its related program files. It does not apply to user data handled by the program, including `sessions.txt`, AI-agent session records, prompts, transcripts, or session results. Those files remain governed by their owners' rights and by any applicable terms of the AI-agent tools or services that created them. Running Aisema does not automatically license, modify, or redistribute that user data under the GPL.

