# notes

This is how I organize all the notes I take throughout a day:

- `meetings/`: meeting notes taken in granola (populated by `granola_sync.py`)
- `sessions/`: notes saved from claude code sessions
- `daily/`: Obsidian daily notes
- `actions/`: action items from the previous workday's meetings (populated by `daily_actions.sh`)

I also have `raycast/ask-notes.sh`, a Raycast script command named `Ask Notes` that answers questions from the notes.

The notes themselves are gitignored.

## Setup

Needs macOS, [uv](https://docs.astral.sh/uv/), and a Granola account (the free plan works). The daily actions and Ask Notes also need the `claude` CLI, and Ask Notes needs `qmd`.

```sh
./granola_sync.py login   # prints a URL and code; confirm the code in the browser
./granola_sync.py         # 2026-09-30 11:56 synced 10 meetings: {'new': 10, 'updated': 0, 'unchanged': 0}
```

Two launchd agents run the scripts (plists in AGENTS.md):

| Agent                        | Runs                                | Log                                |
| ---------------------------- | ----------------------------------- | ---------------------------------- |
| `com.benheath.granola-sync`  | daily at 06:00, then re-indexes qmd | `~/Library/Logs/granola-sync.log`  |
| `com.benheath.daily-actions` | weekdays at 08:00                   | `~/Library/Logs/daily-actions.log` |

For Ask Notes, index the notes with qmd:

```sh
npm install -g @tobilu/qmd
qmd collection add ~/Developer/notes --name notes   # ask-notes.sh expects the name "notes"
qmd embed
```

Then in Raycast settings, open **Extensions**, click **+**, choose **Add Script Directory**, and pick `raycast/`. The script calls `~/.local/bin/qmd`; if qmd is installed under nvm, that path needs a wrapper that loads nvm first (see AGENTS.md).

## Good to know

- Don't edit files in `meetings/` until they're 30 days old; the 06:00 sync overwrites them.
- Notes added during the day aren't searchable by Ask Notes until the 06:00 re-index. Run `qmd update && qmd embed` to index them now.
