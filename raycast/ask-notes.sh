#!/bin/bash

# @raycast.schemaVersion 1
# @raycast.title Ask Notes
# @raycast.mode fullOutput
# @raycast.packageName Notes
# @raycast.icon 🔎
# @raycast.argument1 { "type": "text", "placeholder": "Question" }

cd "$HOME/Developer/notes" || exit 1

printf 'Q: %s\n\n' "$1"

# hybrid keyword + vector search; reranking adds ~7s for little gain on this corpus
notes=$("$HOME/.local/bin/qmd" query --no-rerank "$1" --json -n 5 2>/dev/null |
  /opt/homebrew/bin/jq -r '.[].file | sub("^qmd://notes/"; "")' |
  while IFS= read -r f; do printf '<note path="%s">\n' "$f"; cat "$f"; printf '\n</note>\n'; done)

# one tool-less call without user plugins/hooks is ~3x faster than letting claude search itself
printf '%s' "$notes" | "$HOME/.local/bin/claude" -p "Answer this question using only the notes above. Cite the note paths you used. If the notes don't cover it, say so. Question: $1" \
  --model sonnet \
  --setting-sources project \
  --strict-mcp-config --mcp-config '{"mcpServers":{}}' \
  --tools ""
