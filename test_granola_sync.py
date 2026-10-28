# run: uv run --with "mcp>=2.2,<3" --with pytest pytest test_granola_sync.py
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import granola_sync


class FakeGranola:
    """granola mcp server: honours updated_after on list_meetings, counts calls per tool"""

    def __init__(self, n):
        now = datetime.now().astimezone()
        self.meetings = {
            f"id-{i:02d}": {"title": f"Meeting {i}", "updated_at": now - timedelta(days=1), "notes": f"notes {i}"}
            for i in range(n)
        }
        self.calls = {}

    def touch(self, id, notes):
        self.meetings[id]["notes"] = notes
        self.meetings[id]["updated_at"] = datetime.now().astimezone()

    def xml(self, ids):
        rows = "".join(
            f'<meeting id="{id}" title="{m["title"]}" date="Oct 27, 2026 4:00 PM EDT" '
            f'updated_at="{m["updated_at"].isoformat()}" url="https://notes.granola.ai/d/{id}">'
            f"<known_participants>Ben</known_participants>"
            f"<private_notes></private_notes><summary>{m['notes']}</summary></meeting>"
            for id, m in self.meetings.items()
            if id in ids
        )
        return f'<meetings_data count="{len(ids)}">{rows}</meetings_data>\n<pagination has_more="false" />'

    async def call_tool(self, tool, args):
        self.calls[tool] = self.calls.get(tool, 0) + 1
        if tool == "list_meetings":
            ids = list(self.meetings)
            if args.get("updated_after"):
                after = datetime.fromisoformat(args["updated_after"])
                if after.tzinfo is None:
                    after = after.astimezone()
                ids = [id for id in ids if self.meetings[id]["updated_at"] > after]
            text = self.xml(ids)
        elif tool == "get_meetings":
            assert len(args["meeting_ids"]) <= 10
            text = self.xml(args["meeting_ids"])
        else:
            text = "warning\n" + json.dumps({"transcript": f"transcript of {args['meeting_id']}"})
        return SimpleNamespace(is_error=False, content=[SimpleNamespace(text=text)])


@pytest.fixture
def granola(tmp_path, monkeypatch):
    server = FakeGranola(25)

    @asynccontextmanager
    async def fake_client(url, http_client):
        yield None, None

    class FakeSession:
        def __init__(self, read, write):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def initialize(self):
            pass

        async def call_tool(self, tool, args):
            return await server.call_tool(tool, args)

    monkeypatch.setattr(granola_sync, "OUT_DIR", tmp_path / "meetings")
    monkeypatch.setattr(granola_sync, "access_token", lambda: "token")
    monkeypatch.setattr(granola_sync, "streamable_http_client", fake_client)
    monkeypatch.setattr(granola_sync, "ClientSession", FakeSession)
    return server


def test_first_sync_fetches_every_meeting(granola):
    granola_sync.sync()

    assert granola.calls["get_meetings"] == 3
    assert len(list(granola_sync.OUT_DIR.rglob("*.md"))) == 25


def test_resync_with_no_changes_skips_get_meetings(granola):
    granola_sync.sync()
    granola.calls.clear()

    granola_sync.sync()

    assert granola.calls.get("get_meetings", 0) == 0
    assert granola.calls.get("get_meeting_transcript", 0) == 0


def test_resync_fetches_only_changed_meetings(granola):
    granola_sync.sync()
    granola.calls.clear()
    granola.touch("id-07", "edited notes")

    granola_sync.sync()

    assert granola.calls["get_meetings"] == 1
    saved = next(p for p in granola_sync.OUT_DIR.rglob("*.md") if "granola_id: id-07" in p.read_text())
    assert "edited notes" in saved.read_text()
