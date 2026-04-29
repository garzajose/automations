#!/usr/bin/env python3
"""
notion_todo_sync.py

Scans your Notion "Meeting notes/" page for lines containing '**' markers
(your to-do convention) and appends any new ones to the "To-Dos from Meeting
Notes" database. Safe to run on a schedule — duplicates are skipped.
"""

import os
import re
import sys
from datetime import date

from notion_client import Client
from notion_client.errors import APIResponseError

SOURCE_PAGE_ID = "97e9b3e2-2758-47f7-b8dc-8d30822ed52e"  # "Meeting notes/"
DATABASE_ID    = "48a8bd90-634d-4cf7-bb78-9f86b5380c91"  # "To-Dos from Meeting Notes"

MARKER = "**"
DEFAULT_STATUS = "Not Started"

token = os.environ.get("NOTION_TOKEN")
if not token:
    sys.exit("ERROR: set NOTION_TOKEN env var.")

notion = Client(auth=token)


def walk_blocks(block_id):
    stack = [(block_id, 0)]
    while stack:
        bid, depth = stack.pop()
        cursor = None
        while True:
            kwargs = {"block_id": bid}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = notion.blocks.children.list(**kwargs)
            for b in resp["results"]:
                yield b, depth
                if b.get("has_children"):
                    stack.append((b["id"], depth + 1))
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")


def plain_text(block):
    btype = block.get("type", "")
    data = block.get(btype, {})
    if not isinstance(data, dict):
        return ""
    rt = data.get("rich_text") or data.get("title") or []
    if not isinstance(rt, list):
        return ""
    parts = []
    for t in rt:
        if isinstance(t, dict):
            parts.append(t.get("plain_text", ""))
    return "".join(parts)


DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def extract_date(text):
    m = DATE_RE.search(text)
    return m.group(1) if m else None


def find_todos(page_id):
    todos = []
    current_source = "Meeting notes"
    current_date = None

    for block, _depth in walk_blocks(page_id):
        text = plain_text(block).strip()
        if not text:
            continue

        btype = block.get("type", "")
        is_heading = btype in ("heading_1", "heading_2", "heading_3")
        is_label = (btype == "paragraph" and len(text) < 80 and MARKER not in text)

        if is_heading or is_label:
            current_source = text
            d = extract_date(text)
            if d:
                current_date = d
            if is_heading:
                continue

        if MARKER in text:
            cleaned = text.replace(MARKER, "").strip().rstrip(",").strip()
            if cleaned:
                todos.append({
                    "task": cleaned,
                    "source": current_source,
                    "meeting_date": current_date,
                })
    return todos


def fetch_existing_tasks():
    existing = set()
    cursor = None
    while True:
        kwargs = {"database_id": DATABASE_ID, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.databases.query(**kwargs)
        for page in resp["results"]:
            title_items = page["properties"].get("Task", {}).get("title", [])
            title = "".join(t.get("plain_text", "") for t in title_items).strip()
            if title:
                existing.add(title.lower())
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return existing


def create_todo(todo):
    props = {
        "Task":   {"title":     [{"text": {"content": todo["task"][:2000]}}]},
        "Status": {"select":    {"name": DEFAULT_STATUS}},
        "Source Meeting": {"rich_text": [{"text": {"content": todo["source"][:2000]}}]},
        "Date Captured":  {"date": {"start": date.today().isoformat()}},
    }
    if todo["meeting_date"]:
        props["Meeting Date"] = {"date": {"start": todo["meeting_date"]}}
    notion.pages.create(parent={"database_id": DATABASE_ID}, properties=props)


def main():
    print("Scanning meeting notes...")
    try:
        todos = find_todos(SOURCE_PAGE_ID)
    except APIResponseError as e:
        sys.exit(f"ERROR reading source page: {e}")

    print(f"  Found {len(todos)} item(s) with '{MARKER}' marker.")

    try:
        existing = fetch_existing_tasks()
    except APIResponseError as e:
        sys.exit(f"ERROR reading database: {e}")

    added = 0
    for t in todos:
        if t["task"].lower() in existing:
            continue
        try:
            create_todo(t)
            existing.add(t["task"].lower())
            added += 1
            preview = t["task"][:90] + ("..." if len(t["task"]) > 90 else "")
            print(f"  + {preview}")
        except APIResponseError as e:
            print(f"  ! Failed to add: {t['task'][:60]!r} - {e}")

    print(f"Done. Added {added} new to-do(s).")


if __name__ == "__main__":
    main()