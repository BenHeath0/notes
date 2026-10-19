# granola-sync

Copies Granola meeting notes into local markdown files so they outlive Granola's free-plan 30-day history window.

`granola_sync.py` talks to Granola's official MCP server (`https://mcp.granola.ai/mcp`) directly, with no LLM involved, so notes are written verbatim. Each run fetches every meeting from the last 30 days and writes one file per meeting to `meetings/`. Meetings that age out of the window stay on disk untouched.

## Requirements

- macOS (failure alerts use `osascript`)
- [uv](https://docs.astral.sh/uv/) — the script declares its dependencies inline (`mcp>=2.2,<3`), so `uv` installs them on first run
- A Granola account (the free plan works)

## Setup

1. Log in once:

   ```sh
   ./granola_sync.py login
   ```

   This prints a URL and code. Open the URL, confirm the code, and the command exits with `logged in`.

2. Run a sync:

   ```sh
   ./granola_sync.py
   ```

   Output looks like:

   ```
   2026-09-30 11:56 synced 10 meetings: {'new': 10, 'updated': 0, 'unchanged': 0}
   ```

3. Schedule it (see [Scheduling](#scheduling)).

## How it works

### Auth

`login` uses OAuth dynamic client registration plus the device authorization flow against `https://mcp-auth.granola.ai/oauth2`:

1. Registers a public client named `granola-sync`.
2. Starts a device authorization request (scope `openid offline_access`) and prints the verification URL and code.
3. Polls the token endpoint until the code is approved, then saves the `client_id` and refresh token to `~/.config/granola-sync/auth.json` (mode `600`).

Each sync exchanges the refresh token for a fresh access token. If the server rotates the refresh token, the new one is saved before the sync continues.

The auth file lives outside this directory, so it never ends up in git.

### Fetching

The script opens a Streamable HTTP MCP session and calls three tools:

- `list_meetings` with `time_range: last_30_days`, filtered to meetings Ben captured or is listed as a participant on.
- `get_meetings` for those IDs, in batches of 10 (the tool's maximum).
- `get_meeting_transcript` once per meeting. It returns a short warning preamble followed by a JSON object; only its `transcript` field is kept. A meeting whose transcript call errors (no recording, or the workspace denies transcript access) gets an empty `Transcript` section instead of failing the sync.

The first two return XML-like text (`<meetings_data><meeting ...>`), which is parsed with `xml.etree`. The sync fails if `get_meetings` returns a different number of meetings than `list_meetings` listed.

### Writing

Each meeting becomes `meetings/YYYY-MM-DD/<title>.md`, grouped into one folder per meeting date:

```markdown
---
granola_id: 6ebe35bf-f1e5-40f3-803a-22c2baa98e79
title: "Demo meeting"
date: 2026-09-26T11:12
url: https://notes.granola.ai/d/6ebe35bf-f1e5-40f3-803a-22c2baa98e79
---

# Demo meeting

**Participants:** ...

## Private notes

(what was typed into the Granola notepad)

## Enhanced notes

(Granola's AI summary, verbatim)

## Transcript

(the full verbatim transcript, as `Microphone:` / `System audio (<name>):` lines)
```

- `date` is the meeting's local start time as Granola reports it; the timezone is dropped.
- Characters that are invalid in filenames or Obsidian links (`\ / : * ? " < > | # ^ [ ]`) become `-`.
- Existing files are matched by `granola_id` anywhere under `meetings/`, not by path. A meeting renamed in Granola keeps its original filename, and its content updates.
- If a new meeting's filename is already taken in that date folder by a different meeting (e.g. two `Standup - SFE` on the same day), the first 8 characters of its ID are appended.
- A file is only rewritten when its content changed.

**Local edits are overwritten** while a meeting is still inside the 30-day window, because Granola is treated as the source of truth. The exception is the `Transcript` section: once a file has a non-empty transcript, it is never overwritten, even if Granola later returns an empty or different one. Once a meeting ages out, its file is never touched again.

## Scheduling

A launchd agent runs the sync daily at 18:00. If the Mac is asleep at that time, launchd runs it on wake.

Save this as `~/Library/LaunchAgents/com.benheath.granola-sync.plist`, adjusting the paths if the repo lives elsewhere:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.benheath.granola-sync</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/uv</string>
        <string>run</string>
        <string>--script</string>
        <string>/Users/benheath/Developer/notes/granola_sync.py</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>18</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/benheath/Library/Logs/granola-sync.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/benheath/Library/Logs/granola-sync.log</string>
</dict>
</plist>
```

Then:

```sh
# load and run once to verify
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.benheath.granola-sync.plist
launchctl kickstart gui/$(id -u)/com.benheath.granola-sync
tail ~/Library/Logs/granola-sync.log

# check last exit code
launchctl print gui/$(id -u)/com.benheath.granola-sync | grep 'last exit'

# unload
launchctl bootout gui/$(id -u)/com.benheath.granola-sync
```

## Failures

Any error during a sync shows a macOS notification titled **Granola sync failed** and exits non-zero. The full traceback is in `~/Library/Logs/granola-sync.log`.

| Error | Fix |
| --- | --- |
| `not logged in` | Run `./granola_sync.py login` |
| `token refresh failed` | The refresh token expired or was revoked. Run `./granola_sync.py login` |
| `listed N meetings but fetched M` | Granola returned incomplete results. Rerun; if it persists, the MCP output format may have changed |
| Import errors after an `mcp` upgrade | The SDK API changed. The script pins `mcp>=2.2,<3`; check the pin |

## Limitations

- **Free plan**: only the last 30 days are reachable, and transcripts (`get_meeting_transcript`) are not available, so `Transcript` sections stay empty for new meetings. Transcripts saved before a downgrade are kept. Anything older than 30 days that was never synced can't be recovered this way. Granola's CSV export (Settings → Profile → Generate CSV) covers full history as a one-off backup.
- **Rate limit**: Granola's MCP averages around 100 requests per minute. A sync uses 1 + ⌈meetings / 10⌉ + meetings tool calls (about 50 for a month of meetings), made one at a time, so this is not a concern.
- **Undocumented format**: the XML-like tool output isn't a documented contract. If Granola changes it, parsing may break or return no meetings. Check the `synced N meetings` count in the log.
- **Refresh token lifetime** is not documented by Granola. Expect to rerun `login` occasionally.

## Daily action items

`daily_actions.sh` builds a morning digest of action items from the previous workday's meetings (Friday's on Mondays):

1. Runs `granola_sync.py` to pick up meetings that ended after the 18:00 sync.
2. Pipes every note in `meetings/<day>/`, minus its `Transcript` section, into `claude -p` (Sonnet, no tools, no MCP servers, no user settings) and asks for action items, including ones outside `Next Steps` sections.
3. Writes `actions/<day>.md` as a checklist: Ben's items first, then everyone else's with the owner in bold (`Unassigned` when nobody owns it). Each item links back to its Granola note.
4. Shows a dialog with the item count and an **Open** button that opens the digest.

If there's no `meetings/<day>/` folder, it exits without writing anything. `actions/` is gitignored like `meetings/`.

A second launchd agent, `~/Library/LaunchAgents/com.benheath.daily-actions.plist`, runs it at 08:00 Monday–Friday. It has the same shape as the sync plist above, with `ProgramArguments` set to the script and `StartCalendarInterval` as an array of five dicts (`Weekday` 1–5, `Hour` 8, `Minute` 0). Output goes to `~/Library/Logs/daily-actions.log`.

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.benheath.daily-actions.plist
launchctl kickstart gui/$(id -u)/com.benheath.daily-actions
tail ~/Library/Logs/daily-actions.log
```

The dialog blocks until a button is clicked. If the Mac is off at 08:00, that day's run is skipped; if it's asleep, the run happens on wake.
