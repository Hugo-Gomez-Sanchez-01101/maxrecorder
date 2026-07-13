"""AI summary of a transcript (Mistral via the free NVIDIA API) published as a
page in a Notion calendar database.

The Notion page is created with the meeting title and today's date; the
database's title and date properties are discovered dynamically so it works
with any calendar database regardless of the property names."""

import re
import logging

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

log = logging.getLogger(__name__)


NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL = "mistralai/mistral-medium-3.5-128b"
NOTION_VERSION = "2022-06-28"

# 32 hex chars, with or without UUID dashes.
_NOTION_ID_RE = re.compile(
    r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.I)

SUMMARY_LANGUAGES = {"en": "English", "es": "Spanish"}

SUMMARY_PROMPT = """
You are an assistant that summarizes work recordings (these may be team meetings, solo work sessions, demos, or tests — infer which from the content itself, and reflect that context accurately in the summary rather than assuming a formal team meeting).

Write the summary in {summary_language}, using exactly these sections (as Markdown ## headings):

## Overview
(2-4 sentences. State what kind of session this actually is — e.g. team meeting, solo recording, test/demo — based on the transcript, not assumed.)

## Topics discussed
(Key points per topic. When the speaker enumerates a list of items — e.g. repos, tasks, files — include every item mentioned, even briefly. Do not summarize a long enumeration down to a few examples.)

## Decisions made
(Only formal decisions or clear conclusions. If none were made, write "No se tomaron decisiones formales" / "No formal decisions were made" instead of inventing one.)

## Action items
(Concrete follow-up tasks only, with the owner if mentioned. If none, say so explicitly.)

## Open questions / topics for the next meeting
(Only include this section's assumptions — like "next meeting" — if the transcript implies recurring meetings. Otherwise phrase as "open questions" without assuming a future meeting.)

Prioritize fidelity to the transcript over fitting the template neatly: it's better to say a section doesn't apply than to force content into it.

Transcript:
\"\"\"
{transcript}
\"\"\"
"""


def extract_notion_database_id(link_or_id: str):
    """Returns the Notion database ID (32 hex chars) from a pasted 'copy link
    to view' URL, or from a bare ID. None if nothing that looks like an ID is
    found.

    NOTE: in a link like notion.so/<id>?v=<view_id>, the database ID the API
    needs is the one in the PATH (before the '?'); the 'v' parameter is the
    view ID and is ignored here."""
    text = (link_or_id or "").strip()
    if not text:
        return None
    path = text.split("?", 1)[0]
    matches = _NOTION_ID_RE.findall(path)
    if matches:
        # Last match in the path: links can be notion.so/workspace/Name-<id>.
        return matches[-1].replace("-", "").lower()
    return None


def summarize(transcript: str, nvidia_api_key: str, language: str = "en") -> str:
    """Summarizes the transcript with Mistral via the NVIDIA API, writing the
    summary in the given app language ('en'/'es'). Returns the summary as
    Markdown. Raises RuntimeError with a readable message on failure."""
    if not REQUESTS_AVAILABLE:
        raise RuntimeError("The 'requests' package is not installed (pip install requests)")
    summary_language = SUMMARY_LANGUAGES.get(language, "the same language as the transcript")
    headers = {
        "Authorization": f"Bearer {nvidia_api_key}",
        "Accept": "application/json",
    }
    payload = {
        "model": NVIDIA_MODEL,
        "reasoning_effort": "none",
        "messages": [{"role": "user", "content": SUMMARY_PROMPT.format(
            transcript=transcript, summary_language=summary_language)}],
        "max_tokens": 16384,
        "temperature": 0.3,
        "top_p": 1.0,
        "stream": False,
    }
    try:
        log.info("Requesting summary from %s (%d chars of transcript)",
                 NVIDIA_MODEL, len(transcript))
        resp = requests.post(NVIDIA_URL, headers=headers, json=payload, timeout=180)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        if code == 401:
            raise RuntimeError("NVIDIA API: invalid API key (401)") from e
        raise RuntimeError(f"NVIDIA API error ({code})") from e
    except requests.RequestException as e:
        raise RuntimeError(f"Could not reach the NVIDIA API: {e}") from e
    except (KeyError, IndexError, ValueError) as e:
        raise RuntimeError("Unexpected response from the NVIDIA API") from e


def test_nvidia_key(nvidia_api_key: str):
    """Verifies the NVIDIA/Mistral key with a minimal request. Raises
    RuntimeError with a readable message if it does not work."""
    if not REQUESTS_AVAILABLE:
        raise RuntimeError("'requests' not installed")
    if not nvidia_api_key:
        raise RuntimeError("empty key")
    payload = {
        "model": NVIDIA_MODEL,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 2,
        "stream": False,
    }
    try:
        resp = requests.post(
            NVIDIA_URL,
            headers={"Authorization": f"Bearer {nvidia_api_key}", "Accept": "application/json"},
            json=payload, timeout=30)
        if resp.status_code in (401, 403):
            raise RuntimeError("invalid key")
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"unreachable ({e.__class__.__name__})") from e


def test_notion_key(notion_api_key: str):
    """Verifies the Notion token. Raises RuntimeError if it does not work."""
    if not REQUESTS_AVAILABLE:
        raise RuntimeError("'requests' not installed")
    if not notion_api_key:
        raise RuntimeError("empty key")
    try:
        resp = requests.get("https://api.notion.com/v1/users/me",
                            headers=_notion_headers(notion_api_key), timeout=30)
        if resp.status_code == 401:
            raise RuntimeError("invalid key")
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"unreachable ({e.__class__.__name__})") from e


def test_database(notion_api_key: str, database_id: str):
    """Verifies that the calendar database exists and the integration can see
    it. Raises RuntimeError if not."""
    if not database_id:
        raise RuntimeError("missing calendar link/ID")
    _database_properties(notion_api_key, database_id)


def markdown_to_blocks(md: str) -> list:
    """Converts the summary (## headings, bullets and text) into simple Notion
    blocks. Notion caps children at 100 blocks and rich text at 2000 chars."""
    blocks = []
    for line in md.splitlines():
        line = line.strip()
        if not line:
            continue
        text = {"type": "text", "text": {"content": line.lstrip("#*- ")[:2000]}}
        if line.startswith("## "):
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": line[3:][:2000]}}]},
            })
        elif line.startswith("- ") or line.startswith("* "):
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": line[2:][:2000]}}]},
            })
        else:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [text]},
            })
    return blocks[:100]


def _notion_headers(notion_api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {notion_api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _database_properties(notion_api_key: str, database_id: str):
    """Returns (title_property_name, date_property_name|None) of the database.
    Discovering them makes the integration work with any calendar database,
    whatever its property names ('Name', 'Nombre', 'Fecha', 'Date'...)."""
    resp = requests.get(
        f"https://api.notion.com/v1/databases/{database_id}",
        headers=_notion_headers(notion_api_key), timeout=60)
    if resp.status_code == 404:
        raise RuntimeError(
            "Notion: database not found. Check the calendar link/ID and make "
            "sure your integration has access to that page.")
    if resp.status_code == 401:
        raise RuntimeError("Notion: invalid API key (401)")
    resp.raise_for_status()
    props = resp.json().get("properties", {})
    title_name = None
    date_name = None
    for name, spec in props.items():
        kind = spec.get("type")
        if kind == "title" and title_name is None:
            title_name = name
        elif kind == "date" and date_name is None:
            date_name = name
    if title_name is None:
        raise RuntimeError("Notion: the database has no title property")
    return title_name, date_name


def publish_to_notion(notion_api_key: str, database_id: str,
                      title: str, day, summary_md: str) -> str:
    """Creates a page in the calendar database with the given title, date and
    summary content. Returns the URL of the created page."""
    if not REQUESTS_AVAILABLE:
        raise RuntimeError("The 'requests' package is not installed (pip install requests)")
    try:
        title_prop, date_prop = _database_properties(notion_api_key, database_id)
        properties = {title_prop: {"title": [{"text": {"content": title}}]}}
        if date_prop:
            properties[date_prop] = {"date": {"start": day.isoformat()}}
        payload = {
            "parent": {"database_id": database_id},
            "properties": properties,
            "children": markdown_to_blocks(summary_md),
        }
        resp = requests.post("https://api.notion.com/v1/pages",
                             headers=_notion_headers(notion_api_key),
                             json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json().get("url", "")
    except RuntimeError:
        raise
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        detail = ""
        try:
            detail = e.response.json().get("message", "")[:120]
        except Exception:
            pass
        raise RuntimeError(f"Notion API error ({code}): {detail}") from e
    except requests.RequestException as e:
        raise RuntimeError(f"Could not reach the Notion API: {e}") from e
