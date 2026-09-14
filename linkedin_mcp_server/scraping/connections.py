"""Read-only connection collection and contact enrichment workflows."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from linkedin_mcp_server.core.exceptions import RateLimitError
from linkedin_mcp_server.scraping.capture import (
    CaptureMode,
    CapturePlan,
    SectionCapture,
)
from linkedin_mcp_server.scraping.content import PageContentReader
from linkedin_mcp_server.scraping.contracts import RATE_LIMITED_SECTION_TEXT
from linkedin_mcp_server.scraping.identifiers import person_profile_url
from linkedin_mcp_server.scraping.navigation import PageNavigator
from linkedin_mcp_server.scraping.session import NAV_DELAY, ScrapingSession

logger = logging.getLogger(__name__)

CONNECTIONS_URL = "https://www.linkedin.com/mynetwork/invite-connect/connections/"

_CONNECTIONS_EXPRESSION = """() => {
    const results = [];
    const seen = new Set();
    // Profile hrefs are absent from innerText, so use one generic link selector.
    const connectionLinks = document.querySelectorAll('main a[href*="/in/"]');
    for (const anchor of connectionLinks) {
        const href = anchor.getAttribute('href') || '';
        const match = href.match(/\\/in\\/([^/?#]+)/);
        if (!match || seen.has(match[1])) continue;
        seen.add(match[1]);

        const card = anchor.closest('li') || anchor.parentElement;
        const lines = card
            ? card.innerText.split('\\n').map(line => line.trim()).filter(Boolean)
            : [];
        const anchorLines = (anchor.innerText || '')
          .split('\\n')
          .map((line) => line.trim())
          .filter(Boolean);
        const name = anchorLines[0] || lines[0] || '';
        const headline = lines.find(line => line !== name) || '';
        results.push({username: match[1], name, headline});
    }
    return results;
}"""


def parse_contact_record(profile_text: str, contact_text: str) -> dict[str, str | None]:
    """Parse the stable legacy contact fields from captured section text."""
    result: dict[str, str | None] = {
        "first_name": None,
        "last_name": None,
        "headline": None,
        "location": None,
        "company": None,
        "email": None,
        "phone": None,
        "website": None,
        "birthday": None,
    }

    non_empty = [line.strip() for line in profile_text.splitlines() if line.strip()]
    if non_empty:
        name_parts = non_empty[0].split(None, 1)
        result["first_name"] = name_parts[0]
        result["last_name"] = name_parts[1] if len(name_parts) > 1 else None

    degree_index = next(
        (
            index
            for index, line in enumerate(non_empty)
            if re.match(r"^·\s*\d+(st|nd|rd|th)\+?$", line)
        ),
        None,
    )
    if degree_index is not None and degree_index + 1 < len(non_empty):
        result["headline"] = non_empty[degree_index + 1]
        if degree_index + 2 < len(non_empty):
            candidate = non_empty[degree_index + 2]
            if candidate not in {"·", "Contact info"}:
                result["location"] = candidate

    for index, line in enumerate(non_empty):
        if line == "Contact info" and index + 1 < len(non_empty):
            result["company"] = non_empty[index + 1]
            break

    for field, label in (
        ("email", "Email"),
        ("phone", "Phone"),
        ("website", "Website"),
        ("birthday", "Birthday"),
    ):
        match = re.search(rf"(?:^|\n){re.escape(label)}\s*\n\s*\n\s*(.+)", contact_text)
        if match:
            result[field] = match.group(1).strip()

    return result


class ConnectionExporter:
    """Own the two bounded, read-only connection export workflows."""

    def __init__(
        self,
        session: ScrapingSession,
        navigator: PageNavigator,
        capture: SectionCapture,
        content: PageContentReader,
    ) -> None:
        self._session = session
        self._navigator = navigator
        self._capture = capture
        self._content = content

    async def collect_connections(
        self, *, limit: int = 0, max_scrolls: int = 50
    ) -> dict[str, Any]:
        """Collect connection cards from one bounded connections-page visit."""
        if limit < 0:
            raise ValueError(f"limit must be zero or positive, got {limit}")
        if max_scrolls <= 0:
            raise ValueError(
                f"max_scrolls must be a positive integer, got {max_scrolls}"
            )

        await self._navigator._navigate_to_page(CONNECTIONS_URL)
        await self._session.check_rate_limit()
        await self._session.page.wait_for_selector("main")
        await self._session.dismiss_modal()
        await self._session.scroll_body(pause_time=1.0, max_scrolls=max_scrolls)

        connections: list[dict[str, str]] = await self._session.page.evaluate(
            _CONNECTIONS_EXPRESSION
        )
        if limit:
            connections = connections[:limit]

        raw_section = await self._content.get_page_text()
        return {
            "url": CONNECTIONS_URL,
            "sections": {"connections": raw_section},
            "connections": connections,
            "total": len(connections),
            "pages_visited": [CONNECTIONS_URL],
        }

    async def enrich_contacts(
        self,
        usernames: list[str],
        chunk_size: int = 5,
        chunk_delay: float = 30.0,
        progress_cb: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Capture profile and contact sections in bounded, paced chunks."""
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be a positive integer, got {chunk_size}")
        if chunk_delay < 0:
            raise ValueError(f"chunk_delay must be zero or positive, got {chunk_delay}")

        contacts: list[dict[str, Any]] = []
        failed: list[str] = []
        pages_visited: list[str] = []
        sections: dict[str, str] = {}
        total = len(usernames)
        rate_limited = False

        for chunk_index in range(0, total, chunk_size):
            chunk = usernames[chunk_index : chunk_index + chunk_size]
            for username in chunk:
                profile_url = person_profile_url(username, "/")
                contact_url = person_profile_url(username, "/overlay/contact-info/")
                try:
                    profile = await self._capture.capture(
                        profile_url,
                        "main_profile",
                        CapturePlan(max_scrolls=0),
                    )
                    pages_visited.append(profile_url)
                    sections[f"{username}_main_profile"] = profile.text
                    if profile.text == RATE_LIMITED_SECTION_TEXT:
                        failed.append(username)
                        await self._session.delay(NAV_DELAY)
                        continue

                    contact = await self._capture.capture(
                        contact_url,
                        "contact_info",
                        CapturePlan(mode=CaptureMode.OVERLAY),
                    )
                    pages_visited.append(contact_url)
                    contact_text = (
                        ""
                        if contact.text == RATE_LIMITED_SECTION_TEXT
                        else contact.text
                    )
                    sections[f"{username}_contact_info"] = contact_text
                    contacts.append(
                        {
                            "username": username,
                            **parse_contact_record(profile.text, contact_text),
                            "profile_raw": profile.text,
                            "contact_info_raw": contact_text,
                        }
                    )
                except RateLimitError:
                    logger.warning("Rate limited during contact batch at %s", username)
                    failed.append(username)
                    rate_limited = True
                    break
                except Exception:
                    logger.warning("Failed to scrape %s", username, exc_info=True)
                    failed.append(username)

                await self._session.delay(NAV_DELAY)

            if rate_limited:
                break

            completed = min(chunk_index + len(chunk), total)
            if progress_cb is not None:
                await progress_cb(completed, total)
            if chunk_index + chunk_size < total:
                await self._session.delay(chunk_delay)

        first_url = pages_visited[0] if pages_visited else CONNECTIONS_URL
        return {
            "url": first_url,
            "sections": sections,
            "contacts": contacts,
            "total": len(contacts),
            "failed": failed,
            "rate_limited": rate_limited,
            "pages_visited": pages_visited,
        }
