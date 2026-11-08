# AGENTS

README.md is for humans: what this is, setup, a few gotchas. This file is for agents: internals, exact formats, and edge cases. Put new findings here and give the README at most one line.

## Layout

- Tracked: `granola_sync.py`, `daily_actions.sh`, `raycast/ask-notes.sh`, `README.md`, `AGENTS.md`, `.gitignore`.
- Gitignored: `meetings/`, `actions/`, `sessions/`, `plans/`, `daily/`, `.obsidian` (the folder doubles as an Obsidian vault).
- **The remote (`github.com/BenHeath0/notes`) is public.** The gitignored folders hold private work notes. Never commit them or remove them from `.gitignore`.
- The path `~/Developer/notes` is hard-coded in both launchd plists, `daily_actions.sh`, `raycast/ask-notes.sh`, `~/.config/qmd/index.yml`, and Ben's `save` and `load-note` Claude skills. Moving the folder means updating all of them.
- Files outside the repo: `~/.config/granola-sync/auth.json` (Granola refresh token), `~/Library/LaunchAgents/com.benheath.{granola-sync,daily-actions}.plist`, `~/.config/qmd/index.yml`.

## granola_sync.py

Talks to Granola's official MCP server (`https://mcp.granola.ai/mcp`) directly, with no LLM involved, so notes are written verbatim. Each run lists meetings from the last 30 days that are new or changed since the last successful sync (`list_meetings` with `updated_after`), fetches only those, and writes one file per meeting to `meetings/`. The last sync time lives in `meetings/.last_sync`; it is written only after all files are written, and a missing file means a full sync of the 30-day window. Delete it to force a full resync. Meetings that age out of the window stay on disk untouched. Dependencies are declared inline (`mcp>=2.2,<3`), so `uv run --script` installs them on first run.

### Auth

`login` uses OAuth dynamic client registration plus the device authorization flow against `https://mcp-auth.granola.ai/oauth2`:

1. Registers a public client named `granola-sync`.
2. Starts a device authorization request (scope `openid offline_access`) and prints the verification URL and code.
3. Polls the token endpoint until the code is approved, then saves the `client_id` and refresh token to `~/.config/granola-sync/auth.json` (mode `600`).

Each sync exchanges the refresh token for a fresh access token. If the server rotates the refresh token, the new one is saved before the sync continues. The auth file lives outside the repo, so it never ends up in git.

### Fetching

Opens a Streamable HTTP MCP session and calls three tools:

- `list_meetings` with `time_range: last_30_days`, filtered to meetings Ben captured or is listed as a participant on.
- `get_meetings` for those IDs, in batches of 10 (the tool's maximum).
- `get_meeting_transcript` once per meeting, only when `FETCH_TRANSCRIPTS` is `True`. It is `False` since 2026-11-06 because the account lost paid-plan transcript access on about 2026-10-26; with it off, new meetings get an empty `Transcript` section and saved transcripts are kept. It returns a short warning preamble followed by a JSON object; only its `transcript` field is kept. A meeting whose transcript call errors (no recording, or the workspace denies transcript access) gets an empty `Transcript` section instead of failing the sync.

The first two return XML-like text (`<meetings_data><meeting ...>`), parsed with `xml.etree`. `list_meetings` appends a `<pagination />` element after `</meetings_data>`, so only the `<meetings_data>` element is parsed. The sync fails if `get_meetings` returns a different number of meetings than `list_meetings` listed.

Timing (2026-10-24, 68 meetings): every call under 1.1s, about 35s for a full run, almost all of it transcripts.

### Writing

Each meeting becomes `meetings/YYYY-MM-DD/<title>.md`, one folder per meeting date:

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

**Local edits are overwritten** while a meeting is still inside the 30-day window, because Granola is the source of truth. The exception is the `Transcript` section: once a file has a non-empty transcript, it is never overwritten, even if Granola later returns an empty or different one. Once a meeting ages out, its file is never touched again.

### Failures

Any error before files are written (token refresh or any MCP call) is retried up to 3 times, 60 seconds apart; each retry logs `fetch failed (...), retrying in 60s`. If the last try also fails, the script shows a macOS dialog titled **Granola sync failed** (a dialog, not a notification, so it shows under Do Not Disturb) and re-raises, so the full traceback lands in `~/Library/Logs/granola-sync.log`.

The MCP client runs requests inside anyio task groups, so errors arrive wrapped, sometimes nested two deep, as `ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)`. The dialog unwraps to the first inner exception and shows `<type>: <message>`. In the log, the real error is at the bottom of the traceback.

| Error | Fix |
| --- | --- |
| `not logged in` | Run `./granola_sync.py login` |
| `token refresh failed` | The refresh token expired or was revoked. Run `./granola_sync.py login` |
| `ReadTimeout` or `ReadError` | Granola's server stopped responding mid-session on every try. Happened 4 times in about 20 daily runs (2026-10-04 to 2026-10-24, before the retry was added), and the next run worked each time. Rerun `./granola_sync.py` to catch up sooner |
| `listed N meetings but fetched M` | Granola returned incomplete results. Rerun; if it persists, the MCP output format may have changed |
| `ParseError: junk after document element` | The tool output format changed around `<meetings_data>`. Check what follows the closing tag (this is how `<pagination />` was found) |
| `get_meetings failed: Rate limit reached` | Granola's per-tool limit. Hit on 2026-10-28 08:00, when every run fetched all 73 meetings (8 `get_meetings` calls per try). Runs now fetch only changed meetings; if it recurs, wait the stated time and rerun |
| Import errors after an `mcp` upgrade | The SDK API changed. The script pins `mcp>=2.2,<3`; check the pin |

### Limitations

- **Free plan**: only the last 30 days are reachable, and transcripts (`get_meeting_transcript`) are not available, so `Transcript` sections stay empty for new meetings. Transcripts saved before a downgrade are kept. Anything older than 30 days that was never synced can't be recovered this way. Granola's CSV export (Settings → Profile → Generate CSV) covers full history as a one-off backup.
- **Rate limit**: limits are per user and per tool, shared with Claude sessions using the Granola MCP. Once spent, `get_meetings` allows about one call per 360 seconds. A sync uses 1 + ⌈changed / 10⌉ + changed tool calls, made one at a time.
- **Undocumented format**: the XML-like tool output isn't a documented contract. If Granola changes it, parsing may break or return no meetings. Check the `synced N meetings` count in the log.
- **Refresh token lifetime** is not documented by Granola. Expect to rerun `login` occasionally.

## daily_actions.sh

Builds a digest of action items from the previous workday's meetings (Friday's on Mondays):

1. Runs `granola_sync.py` to pick up meetings that ended after the 06:00 sync.
2. Pipes every note in `meetings/<day>/`, minus its `Transcript` section, into `claude -p` (Sonnet, `--tools ""`, no MCP servers, `--setting-sources project`) and asks for action items, including ones outside `Next Steps` sections.
3. Writes `actions/<day>.md` as a checklist: Ben's items first, then everyone else's with the owner in bold (`Unassigned` when nobody owns it). Each item links back to its Granola note.
4. Shows a dialog with the item count and an **Open** button that opens the digest.

If there's no `meetings/<day>/` folder, it exits without writing anything. The dialog blocks until a button is clicked. If the Mac is off at 08:00, that day's run is skipped; if it's asleep, the run happens on wake.

## raycast/ask-notes.sh

Raycast script command ("Ask Notes", full output mode, one text argument). It does retrieval itself, then makes one Claude call:

1. `qmd query --no-rerank "<question>" --json -n 5` finds the 5 most relevant notes; `jq` strips the `qmd://notes/` prefix to get file paths.
2. The full text of those notes is piped into `claude -p --model sonnet --setting-sources project --strict-mcp-config --mcp-config '{"mcpServers":{}}' --tools ""`, which answers only from them and cites note paths.

Why it's built this way (measured 2026-10-10): about 10-13s per answer, vs 35-58s when Claude searched the notes itself through the qmd MCP server. The tradeoff is no second search if the top 5 miss. `--bare` can't be used because it skips OAuth. Reranking adds about 7s for little gain on this corpus.

## qmd

- The collection `notes` covers `**/*.md` in this folder (`~/.config/qmd/index.yml`), so README.md and AGENTS.md are indexed too. The index is at `~/.cache/qmd/index.sqlite`, models at `~/.cache/qmd/models/`, both outside the repo.
- qmd is npm-installed under nvm, which zsh lazy-loads. Non-interactive callers (Raycast, launchd, the qmd MCP server in Claude Code) must use the wrapper `~/.local/bin/qmd`, which sources nvm first. After changing nvm's default node, reinstall with `npm i -g @tobilu/qmd`. The wrapper:

  ```sh
  #!/bin/sh
  . /opt/homebrew/opt/nvm/nvm.sh
  exec "$NVM_BIN/qmd" "$@"
  ```
- The granola-sync launchd agent re-indexes after each sync (`qmd update && qmd embed`). Manual re-index takes about 1s when nothing changed.
- Search modes on this corpus: `qmd search` (BM25 only) returns nothing for full-sentence questions; `vsearch` takes 2-3s; `query` with rerank about 8s; `query --no-rerank` about 1.7s and still finds exact IDs like ticket keys.

## Scheduling

Both agents live in `~/Library/LaunchAgents/`. If the Mac is asleep at the scheduled time, launchd runs the job on wake.

`com.benheath.granola-sync.plist` (sync, then re-index qmd):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.benheath.granola-sync</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/sh</string>
        <string>-c</string>
        <string>/opt/homebrew/bin/uv run --script /Users/benheath/Developer/notes/granola_sync.py; /Users/benheath/.local/bin/qmd update &amp;&amp; /Users/benheath/.local/bin/qmd embed</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>6</integer>
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

The `;` means qmd re-indexes even when the sync fails.

`com.benheath.daily-actions.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.benheath.daily-actions</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/benheath/Developer/notes/daily_actions.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
    </array>
    <key>StandardOutPath</key>
    <string>/Users/benheath/Library/Logs/daily-actions.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/benheath/Library/Logs/daily-actions.log</string>
</dict>
</plist>
```

Managing either agent (swap the label):

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
