#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.2,<3"]
# ///
import asyncio
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MCP_URL = "https://mcp.granola.ai/mcp"
auth_http = httpx2.Client(base_url="https://mcp-auth.granola.ai/oauth2", timeout=30)
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
AUTH_FILE = Path.home() / ".config" / "granola-sync" / "auth.json"
OUT_DIR = Path(__file__).resolve().parent / "meetings"


def save_auth(client_id, refresh_token):
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(json.dumps({"client_id": client_id, "refresh_token": refresh_token}))
    AUTH_FILE.chmod(0o600)


def login():
    r = auth_http.post("/register", json={
        "client_name": "granola-sync",
        "grant_types": [DEVICE_GRANT, "refresh_token"],
        "token_endpoint_auth_method": "none",
        # required by the server even though the device flow never redirects
        "redirect_uris": ["http://127.0.0.1/callback"],
    })
    r.raise_for_status()
    client_id = r.json()["client_id"]

    r = auth_http.post("/device_authorization", data={
        "client_id": client_id,
        "scope": "openid offline_access",
        "resource": MCP_URL,
    })
    r.raise_for_status()
    device = r.json()
    print(f"open {device.get('verification_uri_complete') or device['verification_uri']} and confirm code {device['user_code']}", flush=True)

    interval = device.get("interval", 5)
    while True:
        time.sleep(interval)
        try:
            r = auth_http.post("/token", data={
                "grant_type": DEVICE_GRANT,
                "device_code": device["device_code"],
                "client_id": client_id,
            })
        except httpx2.TransportError:
            continue
        body = r.json()
        if r.is_success:
            break
        if body.get("error") == "slow_down":
            interval += 5
        elif body.get("error") != "authorization_pending":
            sys.exit(f"login failed: {body}")

    save_auth(client_id, body["refresh_token"])
    print("logged in")


def access_token():
    if not AUTH_FILE.exists():
        raise RuntimeError("not logged in, run: granola_sync.py login")
    auth = json.loads(AUTH_FILE.read_text())
    r = auth_http.post("/token", data={
        "grant_type": "refresh_token",
        "refresh_token": auth["refresh_token"],
        "client_id": auth["client_id"],
        "resource": MCP_URL,
    })
    if not r.is_success:
        raise RuntimeError(f"token refresh failed ({r.status_code}): {r.text}. run: granola_sync.py login")
    tokens = r.json()
    # refresh tokens may rotate, so persist the new one before using the access token
    save_auth(auth["client_id"], tokens.get("refresh_token", auth["refresh_token"]))
    return tokens["access_token"]


async def call(session, tool, args):
    result = await session.call_tool(tool, args)
    text = result.content[0].text
    if result.is_error:
        raise RuntimeError(f"{tool} failed: {text}")
    start = text.find("<meetings_data")
    if start == -1:
        return []
    # list_meetings appends a <pagination /> element after the closing tag
    end = text.find("</meetings_data>") + len("</meetings_data>")
    return ET.fromstring(text[start:end]).findall("meeting")


async def transcript(session, meeting_id):
    result = await session.call_tool("get_meeting_transcript", {"meeting_id": meeting_id})
    text = result.content[0].text
    # output is a warning preamble followed by a json object
    start = text.find("{")
    if result.is_error or start == -1:
        return ""
    return (json.loads(text[start:]).get("transcript") or "").strip()


async def fetch_meetings(token):
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=120)
    async with http, streamable_http_client(MCP_URL, http_client=http) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listing = await call(session, "list_meetings", {
                "time_range": "last_30_days",
                "involvement": {"captured_by_me": True, "listed_as_participant": True},
            })
            ids = [m.get("id") for m in listing]
            meetings = []
            for i in range(0, len(ids), 10):
                meetings += await call(session, "get_meetings", {"meeting_ids": ids[i:i + 10]})
            if len(meetings) != len(ids):
                raise RuntimeError(f"listed {len(ids)} meetings but fetched {len(meetings)}")
            transcripts = {id: await transcript(session, id) for id in ids}
            return meetings, transcripts


def render(m, transcript):
    when = datetime.strptime(m.get("date").rsplit(" ", 1)[0], "%b %d, %Y %I:%M %p")
    title = m.get("title")
    return when, "\n".join([
        "---",
        f"granola_id: {m.get('id')}",
        f"title: {json.dumps(title)}",
        f"date: {when:%Y-%m-%dT%H:%M}",
        f"url: {m.get('url')}",
        "---",
        "",
        f"# {title}",
        "",
        f"**Participants:** {m.findtext('known_participants', '').strip()}",
        "",
        "## Private notes",
        "",
        m.findtext("private_notes", "").strip(),
        "",
        "## Enhanced notes",
        "",
        m.findtext("summary", "").strip(),
        "",
        "## Transcript",
        "",
        transcript,
        "",
    ])


def sync():
    meetings, transcripts = asyncio.run(fetch_meetings(access_token()))
    OUT_DIR.mkdir(exist_ok=True)
    existing = {}
    for path in OUT_DIR.rglob("*.md"):
        match = re.search(r"^granola_id: (\S+)", path.read_text(), re.M)
        if match:
            existing[match[1]] = path

    counts = {"new": 0, "updated": 0, "unchanged": 0}
    for m in meetings:
        transcript = transcripts[m.get("id")]
        path = existing.get(m.get("id"))
        if path:
            # a saved transcript is never overwritten
            saved = path.read_text().split("\n## Transcript\n", 1)
            if len(saved) == 2 and saved[1].strip():
                transcript = saved[1].strip()
        when, content = render(m, transcript)
        if path is None:
            name = re.sub(r'[\\/:*?"<>|#^\[\]]', "-", m.get("title")).strip()
            day_dir = OUT_DIR / f"{when:%Y-%m-%d}"
            path = day_dir / f"{name}.md"
            if path.exists():
                path = day_dir / f"{name} {m.get('id')[:8]}.md"
            counts["new"] += 1
        elif path.read_text() == content:
            counts["unchanged"] += 1
            continue
        else:
            counts["updated"] += 1
        path.parent.mkdir(exist_ok=True)
        path.write_text(content)

    print(f"{datetime.now():%Y-%m-%d %H:%M} synced {len(meetings)} meetings: {counts}")


if __name__ == "__main__":
    if sys.argv[1:] == ["login"]:
        login()
    else:
        try:
            sync()
        except Exception as e:
            subprocess.run(["osascript", "-e", f"display notification {json.dumps(str(e)[:200])} with title \"Granola sync failed\""])
            raise
