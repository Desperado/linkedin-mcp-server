"""Browser-DOM tests for local message-recipient verification.

The unit suite mocks ``page.evaluate``, so these cases execute the extraction,
focus, and submit JavaScript against synthetic Chromium DOMs. No LinkedIn page
is loaded and no account action is performed.
"""

from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock, patch

import pytest
from patchright.async_api import BrowserType, async_playwright

from linkedin_mcp_server.scraping.message_sender import (
    MessageSender,
    _MESSAGE_COMPOSER_STATE_JS,
    _PROFILE_MESSAGE_TARGET_JS,
    _ProfileMessageTarget,
)
from linkedin_mcp_server.scraping.navigation import PageNavigator
from linkedin_mcp_server.scraping.session import ScrapingSession


def _sender(page) -> MessageSender:
    session = ScrapingSession(page)
    return MessageSender(session, PageNavigator(session))


pytestmark = [
    pytest.mark.browser_dom,
    pytest.mark.xdist_group("browser_runtime"),
]

TARGET = {"profilePath": "/in/testuser/", "profileUrn": "ACoAAB"}


def _message_target() -> _ProfileMessageTarget:
    return _ProfileMessageTarget(
        profile_path=TARGET["profilePath"],
        profile_urn=TARGET["profileUrn"],
        compose_url="https://www.linkedin.com/messaging/compose/?recipient=ACoAAB",
        display_name="Test User",
    )


async def _dom_page():
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(
                channel="chromium", headless=True
            )
            page = await browser.new_page()
        except Exception as exc:
            if os.environ.get("CI"):
                raise
            pytest.skip(f"chromium unavailable: {exc}")
        await page.route(
            "https://www.linkedin.com/**",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body='<!DOCTYPE html><html><head><meta charset="utf-8"></head></html>',
            ),
        )
        try:
            yield page
        finally:
            await browser.close()


@pytest.fixture
async def dom_page():
    async for page in _dom_page():
        yield page


async def test_dom_page_launch_failure_raises_in_ci(monkeypatch):
    monkeypatch.setenv("CI", "1")
    fixture = _dom_page()
    with (
        patch.object(
            BrowserType,
            "launch",
            new_callable=AsyncMock,
            side_effect=RuntimeError("injected launch failure"),
        ),
        patch.object(
            pytest,
            "skip",
            side_effect=AssertionError("CI launch failure was skipped"),
        ) as skip,
        pytest.raises(RuntimeError, match="injected launch failure"),
    ):
        await fixture.__anext__()
    skip.assert_not_called()


async def test_dom_page_launch_failure_skips_locally(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    fixture = _dom_page()
    with (
        patch.object(
            BrowserType,
            "launch",
            new_callable=AsyncMock,
            side_effect=RuntimeError("injected launch failure"),
        ),
        pytest.raises(pytest.skip.Exception, match="injected launch failure"),
    ):
        await fixture.__anext__()


def _composer(
    *, identity: str, buttons: str = "", extra: str = "", editor: str = ""
) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
      <main>
        <section role="dialog">
          {identity}
          <form>
            <div role="textbox" contenteditable="true"
                 style="display:block;width:200px;height:30px">{editor}</div>
            {buttons}
          </form>
        </section>
        {extra}
      </main>
    </body></html>
    """


async def _set_composer_content(page, html: str) -> None:
    await page.goto("https://www.linkedin.com/messaging/compose/?recipient=ACoAAB")
    await page.set_content(html)


async def _state(page, html: str) -> dict:
    await _set_composer_content(page, html)
    return await page.evaluate(_MESSAGE_COMPOSER_STATE_JS, TARGET)


class TestMessageSurfaceDom:
    async def test_waits_for_delayed_editor(self, dom_page):
        await _set_composer_content(
            dom_page,
            """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
              <section role="dialog"><form></form></section>
            </body></html>""",
        )
        dom_page.set_default_timeout(1_000)
        await dom_page.evaluate(
            """() => setTimeout(() => {
                document.querySelector('form').insertAdjacentHTML(
                    'beforeend',
                    '<div role="textbox" contenteditable="true" '
                    + 'style="display:block;width:200px;height:30px"></div>'
                );
            }, 250)"""
        )
        started = time.monotonic()

        result = await _sender(dom_page)._wait_for_message_surface(_message_target())

        assert result == "composer"
        assert time.monotonic() - started >= 0.2

    async def test_waits_for_multiple_editors_to_settle(self, dom_page):
        await _set_composer_content(
            dom_page,
            _composer(
                identity='<a href="https://www.linkedin.com/in/testuser/">Test</a>',
                extra=(
                    '<div role="textbox" contenteditable="true" data-stale '
                    'style="display:block;width:200px;height:30px"></div>'
                ),
            ),
        )
        dom_page.set_default_timeout(1_000)
        await dom_page.evaluate(
            """() => setTimeout(() => {
                document.querySelector('[data-stale]').remove();
            }, 250)"""
        )
        started = time.monotonic()

        result = await _sender(dom_page)._wait_for_message_surface(_message_target())

        assert result == "composer"
        assert time.monotonic() - started >= 0.2

    async def test_permanent_recipient_conflict_times_out(self, dom_page):
        await _set_composer_content(
            dom_page,
            """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
              <section role="dialog" data-recipient-urn="OTHER">
                <form>
                  <a href="https://www.linkedin.com/in/testuser/">Test</a>
                  <div role="textbox" contenteditable="true"
                       style="display:block;width:200px;height:30px"></div>
                </form>
              </section>
            </body></html>""",
        )
        dom_page.set_default_timeout(350)
        started = time.monotonic()

        result = await _sender(dom_page)._wait_for_message_surface(_message_target())

        assert result is None
        assert time.monotonic() - started >= 0.25
        # `active` and `empty` both read false wherever the inspect never
        # settled on an editor: they answer for one, and there is none.
        assert await dom_page.evaluate(_MESSAGE_COMPOSER_STATE_JS, TARGET) == {
            "status": "recipient_mismatch",
            "active": False,
            "empty": False,
            "submitCount": 0,
            "enterToSend": False,
            "submitUsable": False,
        }


class TestProfileMessageTargetDom:
    async def test_snapshot_stays_inside_first_top_card(self, dom_page):
        await _set_composer_content(
            dom_page,
            """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><main>
              <section>
                <h1>Test User</h1>
                <a style="display:block" href="/messaging/compose/?recipient=ACoAAB">
                  Nachricht
                </a>
              </section>
              <section>
                <a style="display:block" href="/messaging/compose/?recipient=OTHER">
                  Sidebar
                </a>
              </section>
            </main></body></html>
            """,
        )

        result = await dom_page.evaluate(_PROFILE_MESSAGE_TARGET_JS)

        assert result["displayName"] == "Test User"
        assert result["composeHrefs"] == ["/messaging/compose/?recipient=ACoAAB"]

    async def test_wrapped_top_card_with_h2_name_resolves(self, dom_page):
        # September 2026 layout: <main> holds one wrapper div, the top card
        # sits several levels down, the name is an <h2>, and the Message
        # action is rendered twice for the same recipient (one hidden).
        await _set_composer_content(
            dom_page,
            """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><main>
              <div class="_70a6f2dc"><div><div>
                <div><section>
                  <h2>Test User</h2>
                  <p>UX advisor</p>
                  <a style="display:block" href="/messaging/compose/?profileUrn=urn%3Ali%3Afsd_profile%3AACoAAB&recipient=ACoAAB">
                    Message
                  </a>
                  <a style="display:none" href="/messaging/compose/?profileUrn=urn%3Ali%3Afsd_profile%3AACoAAB&recipient=ACoAAB">
                    Message
                  </a>
                </section></div>
                <section><h2>Ads</h2><p>Sponsored</p></section>
                <section><h2>About</h2><p>Bio</p></section>
                <section>
                  <a style="display:block" href="/messaging/compose/?recipient=OTHER">
                    Sidebar
                  </a>
                </section>
              </div></div></div>
            </main></body></html>
            """,
        )

        result = await dom_page.evaluate(_PROFILE_MESSAGE_TARGET_JS)

        assert result["status"] == "resolved"
        assert result["displayName"] == "Test User"
        assert result["composeHrefs"] == [
            "/messaging/compose/?profileUrn=urn%3Ali%3Afsd_profile%3AACoAAB&recipient=ACoAAB"
        ]

    async def test_structural_section_before_wrapped_top_card_is_ignored(
        self, dom_page
    ):
        # The first descendant section can be a heading-free utility shell;
        # the profile card is the first section that owns a page heading.
        await _set_composer_content(
            dom_page,
            """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><main>
              <div>
                <section><div role="presentation"></div></section>
                <div><section>
                  <a href="/in/test-user/"><div><h2>Test User</h2></div></a>
                  <a style="display:block" href="/messaging/compose/?profileUrn=urn%3Ali%3Afsd_profile%3AACoAAB">
                    Message
                  </a>
                </section></div>
                <section><h2>Other User</h2>
                  <a style="display:block" href="/messaging/compose/?recipient=OTHER">
                    Message
                  </a>
                </section>
              </div>
            </main></body></html>
            """,
        )

        result = await dom_page.evaluate(_PROFILE_MESSAGE_TARGET_JS)

        assert result["status"] == "resolved"
        assert result["displayName"] == "Test User"
        assert result["composeHrefs"] == [
            "/messaging/compose/?profileUrn=urn%3Ali%3Afsd_profile%3AACoAAB"
        ]

    async def test_aria_level_two_profile_heading_resolves(self, dom_page):
        await _set_composer_content(
            dom_page,
            """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><main>
              <div><section>
                <div role="heading" aria-level="2">Test User</div>
                <a style="display:block" href="/messaging/compose/?recipient=ACoAAB">
                  Message
                </a>
              </section></div>
            </main></body></html>
            """,
        )

        result = await dom_page.evaluate(_PROFILE_MESSAGE_TARGET_JS)

        assert result["status"] == "resolved"
        assert result["displayName"] == "Test User"
        assert result["composeHrefs"] == ["/messaging/compose/?recipient=ACoAAB"]

    async def test_section_ancestor_outside_main_cannot_supply_action(self, dom_page):
        await _set_composer_content(
            dom_page,
            """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
              <section>
                <main><h2>Test User</h2></main>
                <a style="display:block" href="/messaging/compose/?recipient=OTHER">
                  Outside main
                </a>
              </section>
            </body></html>
            """,
        )

        result = await dom_page.evaluate(_PROFILE_MESSAGE_TARGET_JS)

        assert result["status"] == "unresolved"

    async def test_later_h2_card_cannot_supply_missing_top_card_action(self, dom_page):
        await _set_composer_content(
            dom_page,
            """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><main>
              <div>
                <section><h2>Test User</h2><p>Profile owner</p></section>
                <section><h2>Other User</h2>
                  <a style="display:block" href="/messaging/compose/?recipient=OTHER">
                    Message
                  </a>
                </section>
              </div>
            </main></body></html>
            """,
        )

        result = await dom_page.evaluate(_PROFILE_MESSAGE_TARGET_JS)

        assert result["status"] == "unavailable"

    @pytest.mark.parametrize(
        "owner_card",
        [
            "<section><h3>Test User</h3><p>Profile owner</p></section>",
            (
                '<section style="display:contents"><h2>Test User</h2>'
                '<a href="/messaging/compose/?recipient=ACoAAB">Message</a></section>'
            ),
        ],
        ids=["unrecognised-heading", "boxless-section"],
    )
    async def test_later_named_card_cannot_replace_unrecognised_owner_card(
        self, dom_page, owner_card
    ):
        await _set_composer_content(
            dom_page,
            f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><main>
              <div>
                {owner_card}
                <section><h2>Other User</h2>
                  <a style="display:block" href="/messaging/compose/?recipient=OTHER">
                    Message
                  </a>
                </section>
              </div>
            </main></body></html>
            """,
        )

        result = await dom_page.evaluate(_PROFILE_MESSAGE_TARGET_JS)

        assert result["status"] == "unresolved"

    async def test_duplicate_visible_actions_for_one_recipient_resolve(self, dom_page):
        await _set_composer_content(
            dom_page,
            """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><main>
              <div><section>
                <h1>Test User</h1>
                <a style="display:block" href="/messaging/compose/?recipient=ACoAAB">
                  Message
                </a>
                <div class="highlights">
                  <a style="display:block" href="/messaging/compose/?profileUrn=urn%3Ali%3Afsd_profile%3AACoAAB">
                    Message
                  </a>
                </div>
              </section></div>
            </main></body></html>
            """,
        )

        result = await dom_page.evaluate(_PROFILE_MESSAGE_TARGET_JS)

        assert result["status"] == "resolved"
        assert result["composeHrefs"] == ["/messaging/compose/?recipient=ACoAAB"]

    async def test_visible_actions_for_different_recipients_stay_unresolved(
        self, dom_page
    ):
        await _set_composer_content(
            dom_page,
            """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><main>
              <div><section>
                <h2>Test User</h2>
                <a style="display:block" href="/messaging/compose/?recipient=ACoAAB">
                  Message
                </a>
                <a style="display:block" href="/messaging/compose/?recipient=OTHER">
                  Message
                </a>
              </section></div>
            </main></body></html>
            """,
        )

        result = await dom_page.evaluate(_PROFILE_MESSAGE_TARGET_JS)

        assert result["status"] == "unresolved"

    async def test_unrecognised_layout_stays_unresolved(self, dom_page):
        # A page the probe cannot classify (name in an <h3>, no heading in
        # the section that owns the action) is an extraction failure. It
        # must not be reported as unavailable, which tells the caller to
        # connect first and stop retrying.
        await _set_composer_content(
            dom_page,
            """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><main>
              <div><section>
                <h3>Test User</h3>
                <a style="display:block" href="/messaging/compose/?recipient=ACoAAB">
                  Message
                </a>
              </section></div>
            </main></body></html>
            """,
        )

        result = await dom_page.evaluate(_PROFILE_MESSAGE_TARGET_JS)

        assert result["status"] == "unresolved"

    async def test_top_card_without_visible_action_is_unavailable(self, dom_page):
        await _set_composer_content(
            dom_page,
            """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><main>
              <div><section>
                <h2>Test User</h2>
                <a style="display:none" href="/messaging/compose/?recipient=ACoAAB">
                  Message
                </a>
              </section></div>
            </main></body></html>
            """,
        )

        result = await dom_page.evaluate(_PROFILE_MESSAGE_TARGET_JS)

        assert result["status"] == "unavailable"


class TestMessageComposerDom:
    async def test_owner_handle_pins_one_dom_instance_and_disposes(self, dom_page):
        await _set_composer_content(
            dom_page,
            _composer(
                identity='<a href="https://www.linkedin.com/in/testuser/">Test</a>',
                buttons='<button type="submit">Send</button>',
            ),
        )
        sender = _sender(dom_page)
        owner = await sender._resolve_message_owner(
            _message_target(), expected_route=dom_page.url
        )

        assert owner is not None
        assert owner.as_element() is not None
        await dom_page.evaluate(
            """() => {
                const owner = document.querySelector('[role="dialog"]');
                owner.replaceWith(owner.cloneNode(true));
            }"""
        )
        assert await owner.evaluate("node => node.isConnected") is False
        await sender._dispose_message_owner(owner)
        with pytest.raises(Exception, match="closed"):
            await owner.evaluate("node => node.isConnected")

    async def test_generic_data_urn_never_authorizes_recipient(self, dom_page):
        state = await _state(
            dom_page,
            _composer(identity='<span data-urn="ACoAAB">unrelated generic data</span>'),
        )

        assert state["status"] == "valid"

    async def test_native_dialog_accepts_explicit_recipient_urn(self, dom_page):
        state = await _state(
            dom_page,
            """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
              <dialog open>
                <span data-recipient-urn="ACoAAB">Test</span>
                <div role="textbox" contenteditable="true"
                     style="display:block;width:200px;height:30px"></div>
              </dialog>
            </body></html>""",
        )

        assert state["status"] == "valid"

    @pytest.mark.parametrize("visibility", ["hidden", "collapse"])
    async def test_invisible_conflicts_do_not_block_url_pinned_composer(
        self, dom_page, visibility
    ):
        await _set_composer_content(
            dom_page,
            _composer(
                identity=(
                    f'<div style="visibility:{visibility}" data-recipient-urn="OTHER">'
                    '<a href="https://www.linkedin.com/in/bob/">Bob</a></div>'
                ),
                buttons='<button type="submit">Send</button>',
            ),
        )
        sender = _sender(dom_page)

        owner = await sender._resolve_message_owner(
            _message_target(), expected_route=dom_page.url
        )
        assert owner is not None
        assert (
            await sender._write_verified_message(
                "Hello!", target=_message_target(), owner=owner
            )
            == "written"
        )
        await sender._dispose_message_owner(owner)

    async def test_hidden_conflicts_do_not_override_visible_identity(self, dom_page):
        await _set_composer_content(
            dom_page,
            _composer(
                identity=(
                    '<div style="visibility:hidden" data-recipient-urn="OTHER">'
                    '<a href="https://www.linkedin.com/in/bob/">Bob</a></div>'
                    '<div data-recipient-urn="ACoAAB">'
                    '<a href="https://www.linkedin.com/in/testuser/">Test</a></div>'
                ),
                buttons='<button type="submit">Send</button>',
            ),
        )
        sender = _sender(dom_page)

        owner = await sender._resolve_message_owner(
            _message_target(), expected_route=dom_page.url
        )
        assert owner is not None
        assert (
            await sender._write_verified_message(
                "Hello!", target=_message_target(), owner=owner
            )
            == "written"
        )
        await sender._dispose_message_owner(owner)

    async def test_second_urn_attribute_on_one_element_is_never_skipped(self, dom_page):
        conflicting = await _state(
            dom_page,
            _composer(
                identity=(
                    '<span data-profile-urn="ACoAAB" data-recipient-urn="OTHER">'
                    "Test</span>"
                )
            ),
        )
        empty = await _state(
            dom_page,
            _composer(
                identity=(
                    '<span data-profile-urn="ACoAAB" data-recipient-urn="">Test</span>'
                )
            ),
        )
        consistent = await _state(
            dom_page,
            _composer(
                identity=(
                    '<span data-profile-urn="ACoAAB" '
                    'data-recipient-urn="urn:li:fsd_profile:ACoAAB">Test</span>'
                )
            ),
        )

        assert conflicting["status"] == "recipient_mismatch"
        assert empty["status"] == "recipient_mismatch"
        assert consistent["status"] == "valid"

    async def test_nested_owner_conflict_fails_closed(self, dom_page):
        state = await _state(
            dom_page,
            """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
              <section role="dialog" data-recipient-urn="OTHER">
                <form>
                  <a href="https://www.linkedin.com/in/testuser/">Test</a>
                  <div role="textbox" contenteditable="true"
                       style="display:block;width:200px;height:30px"></div>
                  <button type="submit">Local</button>
                </form>
                <button type="submit" data-outer>Outer</button>
              </section>
            </body></html>""",
        )

        assert state["status"] == "recipient_mismatch"
        assert state["submitCount"] == 0

    async def test_nested_consistent_identity_keeps_submit_local(self, dom_page):
        await _set_composer_content(
            dom_page,
            """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
              <section role="dialog" data-recipient-urn="ACoAAB">
                <form>
                  <a href="https://www.linkedin.com/in/testuser/">Test</a>
                  <div role="textbox" contenteditable="true"
                       style="display:block;width:200px;height:30px"></div>
                  <button type="submit" onclick="event.preventDefault();
                    this.setAttribute('data-clicked','yes')">Local</button>
                </form>
                <button type="submit" data-outer>Outer</button>
              </section>
            </body></html>""",
        )

        sender = _sender(dom_page)
        owner = await sender._resolve_message_owner(
            _message_target(), expected_route=dom_page.url
        )
        assert owner is not None
        written = await sender._write_verified_message(
            "Hello!", target=_message_target(), owner=owner
        )
        submitted = await sender._submit_verified_message(
            "Hello!", target=_message_target(), owner=owner
        )
        await sender._dispose_message_owner(owner)

        assert written == "written"
        assert submitted == "clicked"
        assert (
            await dom_page.locator("form button").get_attribute("data-clicked") == "yes"
        )
        assert (
            await dom_page.locator("[data-outer]").get_attribute("data-clicked") is None
        )

    @pytest.mark.parametrize(
        ("recipient_urn", "button_form", "expected"),
        [
            ("OTHER", None, None),
            ("ACoAAB", "foreign", None),
            ("ACoAAB", None, ("written", "clicked", "ancestor")),
        ],
        ids=["foreign-ancestor", "foreign-form-owner", "matching-ancestor"],
    )
    async def test_entire_semantic_ancestor_chain_controls_submit(
        self, dom_page, recipient_urn, button_form, expected
    ):
        form_attribute = f' form="{button_form}"' if button_form else ""
        await _set_composer_content(
            dom_page,
            f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
              <form id="ancestor" data-recipient-urn="{recipient_urn}"
                    onsubmit="event.preventDefault();
                      document.body.dataset.submitted=this.id">
                <section role="dialog">
                  <div role="textbox" contenteditable="true"
                       style="display:block;width:200px;height:30px"></div>
                  <button type="submit"{form_attribute}>Send</button>
                </section>
              </form>
              <form id="foreign" onsubmit="event.preventDefault();
                document.body.dataset.submitted=this.id"></form>
            </body></html>""",
        )
        sender = _sender(dom_page)
        owner = await sender._resolve_message_owner(
            _message_target(), expected_route=dom_page.url
        )
        written = submitted = None
        if owner is not None:
            written = await sender._write_verified_message(
                "Hello!", target=_message_target(), owner=owner
            )
            submitted = await sender._submit_verified_message(
                "Hello!", target=_message_target(), owner=owner
            )
            await sender._dispose_message_owner(owner)

        if expected is None:
            assert owner is None
            assert written is None
            assert submitted is None
            assert await dom_page.evaluate("document.body.dataset.submitted") is None
        else:
            assert (written, submitted) == expected[:2]
            assert (
                await dom_page.evaluate("document.body.dataset.submitted")
                == expected[2]
            )

    async def test_profile_link_inside_editor_never_authorizes(self, dom_page):
        """Draft content is not a recipient, however well-formed it looks."""
        state = await _state(
            dom_page,
            """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
              <section role="dialog"><form>
                <div role="textbox" contenteditable="true"
                     style="display:block;width:200px;height:30px">
                  <a href="https://www.linkedin.com/in/testuser/">Draft link</a>
                </div>
              </form></section>
            </body></html>""",
        )

        assert state["status"] == "valid"

    @pytest.mark.parametrize("attribute", ["data-profile-urn", "data-recipient-urn"])
    @pytest.mark.parametrize("location", ["editor", "descendant"])
    async def test_recipient_urn_inside_editor_never_authorizes(
        self, dom_page, attribute, location
    ):
        editor_attribute = f'{attribute}="ACoAAB"' if location == "editor" else ""
        draft = (
            f'<span {attribute}="ACoAAB">Draft identity</span>'
            if location == "descendant"
            else "Draft"
        )
        state = await _state(
            dom_page,
            f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
              <section role="dialog"><form>
                <div role="textbox" contenteditable="true" {editor_attribute}
                     style="display:block;width:200px;height:30px">{draft}</div>
              </form></section>
            </body></html>""",
        )

        assert state["status"] == "valid"

    @pytest.mark.parametrize(
        "draft_identity",
        [
            '<a href="https://www.linkedin.com/in/testuser/">Matching draft</a>',
            '<a href="https://www.linkedin.com/in/other/">Foreign draft</a>',
            '<span data-profile-urn="ACoAAB">Matching draft</span>',
            '<span data-profile-urn="OTHER">Foreign draft</span>',
            '<span data-recipient-urn="ACoAAB">Matching draft</span>',
            '<span data-recipient-urn="OTHER">Foreign draft</span>',
        ],
    )
    async def test_outer_identity_ignores_draft_identity(
        self, dom_page, draft_identity
    ):
        """The verdict comes from the recipient chip, never from the draft."""
        state = await _state(
            dom_page,
            f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
              <section role="dialog"><form>
                <a href="https://www.linkedin.com/in/testuser/">Recipient</a>
                <div role="textbox" contenteditable="true"
                     style="display:block;width:200px;height:30px">
                  {draft_identity}
                </div>
              </form></section>
            </body></html>""",
        )

        assert state["status"] == "valid"

    async def test_name_only_or_global_identity_never_authorizes(self, dom_page):
        html = _composer(
            identity=(
                "<span>Test User</span>"
                '<span hidden data-profile-urn="ACoAAB">stale identity</span>'
            ),
            extra='<a href="https://www.linkedin.com/in/testuser/">Test User</a>',
        )

        state = await _state(dom_page, html)

        assert state["status"] == "valid"

    async def test_foreign_or_multiple_editor_fails_closed(self, dom_page):
        foreign = await _state(
            dom_page,
            _composer(
                identity='<a href="https://www.linkedin.com/in/other/">Other</a>'
            ),
        )
        multiple = await _state(
            dom_page,
            _composer(
                identity='<a href="https://www.linkedin.com/in/testuser/">Test</a>',
                extra=(
                    '<div role="textbox" contenteditable="true" '
                    'style="display:block;width:200px;height:30px"></div>'
                ),
            ),
        )

        assert foreign["status"] == "recipient_mismatch"
        assert multiple["status"] == "ambiguous_editor"

    @pytest.mark.parametrize(
        ("href", "expected"),
        [
            # Every shape below was read off one live profile page. Four of
            # its 38 visible anchors point into the member's own subtree, and
            # reading those as an unknown member made the recipient
            # contradict themselves and stopped the send.
            ("https://www.linkedin.com/in/testuser/overlay/contact-info/", "valid"),
            ("https://www.linkedin.com/in/testuser/recent-activity/all/", "valid"),
            ("https://www.linkedin.com/in/testuser", "valid"),
            ("https://www.linkedin.com/in/testuser?miniProfileUrn=urn%3A", "valid"),
            # A different member stays a different member in every shape.
            ("https://www.linkedin.com/in/other/en/", "recipient_mismatch"),
            ("https://www.linkedin.com/in/other", "recipient_mismatch"),
            # An anchor that names nobody stays a contradiction rather than
            # silence: a link the check cannot read is not evidence that the
            # recipient is the requested one.
            ("https://www.linkedin.com/in/", "recipient_mismatch"),
            ("https://evil.example/in/testuser/", "recipient_mismatch"),
        ],
    )
    async def test_subpaths_belong_to_the_member_they_sit_under(
        self, dom_page, href, expected
    ):
        state = await _state(
            dom_page, _composer(identity=f'<a href="{href}">Profile</a>')
        )

        assert state["status"] == expected

    async def test_focus_and_single_submit_stay_local(self, dom_page):
        await _set_composer_content(
            dom_page,
            _composer(
                identity='<a href="https://www.linkedin.com/in/testuser/">Test</a>',
                buttons=(
                    '<button type="submit" onclick="event.preventDefault();'
                    "this.setAttribute('data-clicked','yes')\">Senden</button>"
                ),
                extra=('<button type="submit" data-global="true">Global</button>'),
            ),
        )

        sender = _sender(dom_page)
        owner = await sender._resolve_message_owner(
            _message_target(), expected_route=dom_page.url
        )
        assert owner is not None
        written = await sender._write_verified_message(
            "Hello!", target=_message_target(), owner=owner
        )
        submitted = await sender._submit_verified_message(
            "Hello!", target=_message_target(), owner=owner
        )
        await sender._dispose_message_owner(owner)

        assert written == "written"
        assert submitted == "clicked"
        assert (
            await dom_page.locator("form button").get_attribute("data-clicked") == "yes"
        )
        assert (
            await dom_page.locator("[data-global]").get_attribute("data-clicked")
            is None
        )

    async def test_ambiguous_submit_is_never_pinned(self, dom_page):
        await _set_composer_content(
            dom_page,
            _composer(
                identity='<a href="https://www.linkedin.com/in/testuser/">Test</a>',
                buttons=(
                    '<button type="submit">A</button><button type="submit">B</button>'
                ),
            ),
        )

        assert (
            await _sender(dom_page)._resolve_message_owner(
                _message_target(), expected_route=dom_page.url
            )
            is None
        )

    @pytest.mark.parametrize("disabled_attribute", ["disabled", 'aria-disabled="true"'])
    async def test_unique_disabled_submit_is_pinned_for_local_write(
        self, dom_page, disabled_attribute
    ):
        await _set_composer_content(
            dom_page,
            _composer(
                identity='<a href="https://www.linkedin.com/in/testuser/">Test</a>',
                buttons=f'<button type="submit" {disabled_attribute}>A</button>',
            ),
        )
        sender = _sender(dom_page)

        owner = await sender._resolve_message_owner(
            _message_target(), expected_route=dom_page.url
        )

        assert owner is not None
        assert (
            await sender._write_verified_message(
                "Hello!", target=_message_target(), owner=owner
            )
            == "written"
        )
        assert (
            await sender._submit_verified_message(
                "Hello!", target=_message_target(), owner=owner
            )
            == "invalid"
        )
        await sender._cleanup_owned_message("Hello!", owner)
        await sender._dispose_message_owner(owner)
        assert await dom_page.locator('[role="textbox"]').inner_text() == ""

    async def test_removed_submit_or_changed_recipient_invalidates_pins(self, dom_page):
        identity = '<a href="https://www.linkedin.com/in/testuser/">Test</a>'
        await _set_composer_content(
            dom_page,
            _composer(identity=identity, buttons='<button type="submit">Send</button>'),
        )
        sender = _sender(dom_page)
        owner = await sender._resolve_message_owner(
            _message_target(), expected_route=dom_page.url
        )
        assert owner is not None
        assert (
            await sender._write_verified_message(
                "Hello!", target=_message_target(), owner=owner
            )
            == "written"
        )
        await dom_page.locator("form button").evaluate("element => element.remove()")
        assert (
            await sender._submit_verified_message(
                "Hello!", target=_message_target(), owner=owner
            )
            == "invalid"
        )
        await sender._dispose_message_owner(owner)

        await _set_composer_content(
            dom_page,
            _composer(identity=identity, buttons='<button type="submit">Send</button>'),
        )
        owner = await sender._resolve_message_owner(
            _message_target(), expected_route=dom_page.url
        )
        assert owner is not None
        assert (
            await sender._write_verified_message(
                "Hello!", target=_message_target(), owner=owner
            )
            == "written"
        )
        await dom_page.locator('[role="dialog"] > a').evaluate(
            "element => element.setAttribute('href', 'https://www.linkedin.com/in/other/')"
        )
        assert (
            await sender._submit_verified_message(
                "Hello!", target=_message_target(), owner=owner
            )
            == "invalid"
        )
        await sender._dispose_message_owner(owner)

    async def test_queryless_thread_route_is_pinned_exactly(self, dom_page):
        await dom_page.goto("https://www.linkedin.com/messaging/thread/INITIAL/")
        await dom_page.set_content(
            _composer(identity="", buttons='<button type="submit">Send</button>')
        )
        sender = _sender(dom_page)
        owner = await sender._resolve_message_owner(
            _message_target(), expected_route=dom_page.url
        )

        assert owner is not None
        assert (
            await sender._write_verified_message(
                "Hello!", target=_message_target(), owner=owner
            )
            == "written"
        )
        await dom_page.evaluate(
            "history.replaceState({}, '', '/messaging/thread/OTHER/')"
        )
        assert (
            await sender._submit_verified_message(
                "Hello!", target=_message_target(), owner=owner
            )
            == "invalid"
        )
        await sender._cleanup_owned_message("Hello!", owner)
        await sender._dispose_message_owner(owner)
        assert await dom_page.locator('[role="textbox"]').inner_text() == ""

    async def test_cleanup_leaves_replaced_draft_untouched(self, dom_page):
        await _set_composer_content(
            dom_page,
            _composer(
                identity='<a href="https://www.linkedin.com/in/testuser/">Test</a>',
                buttons='<button type="submit">Send</button>',
            ),
        )
        sender = _sender(dom_page)
        owner = await sender._resolve_message_owner(
            _message_target(), expected_route=dom_page.url
        )
        assert owner is not None
        assert (
            await sender._write_verified_message(
                "Hello!", target=_message_target(), owner=owner
            )
            == "written"
        )
        await dom_page.locator('[role="textbox"]').evaluate(
            "editor => editor.replaceChildren('Author draft')"
        )

        await sender._cleanup_owned_message("Hello!", owner)
        await sender._dispose_message_owner(owner)

        assert await dom_page.locator('[role="textbox"]').inner_text() == "Author draft"

    async def test_missing_submit_never_falls_back_to_enter(self, dom_page):
        await _set_composer_content(
            dom_page,
            _composer(
                identity=(
                    '<span data-profile-urn="urn:li:fsd_profile:ACoAAB">Test</span>'
                ),
                extra='<input id="foreign">',
            ),
        )
        await dom_page.locator("#foreign").evaluate(
            "element => element.addEventListener('keydown', () => "
            "document.body.dataset.foreignKey = 'true')"
        )
        await dom_page.locator("#foreign").focus()

        assert (
            await _sender(dom_page)._resolve_message_owner(
                _message_target(), expected_route=dom_page.url
            )
            is None
        )
        assert await dom_page.evaluate("document.body.dataset.foreignKey") is None


# The empty LinkedIn composer holds one empty paragraph, `<p><br></p>`
# (measured live in a recipient-less probe, September 2026). Inserting a
# multi-line message there with `insertText` gives `<p>` per line and
# `<p><br></p>` per empty line, as it did live; the shapes below are what this
# Chromium produces from that start, not copies of LinkedIn markup.
_EMPTY_PARAGRAPH = "<p><br></p>"

_COUNTING_BUTTON = (
    '<button type="submit" onclick="event.preventDefault();'
    "document.body.dataset.clicks = Number(document.body.dataset.clicks || 0) + 1"
    '">Send</button>'
)


class TestMultiLineComposerDom:
    @staticmethod
    async def _open(page):
        await _set_composer_content(
            page,
            _composer(
                identity='<a href="https://www.linkedin.com/in/testuser/">Test</a>',
                buttons=_COUNTING_BUTTON,
                editor=_EMPTY_PARAGRAPH,
            ),
        )
        await page.evaluate(
            """() => {
                const count = name => {
                    document.body.dataset[name] =
                        Number(document.body.dataset[name] || 0) + 1;
                };
                document.querySelector('form').addEventListener('submit', event => {
                    event.preventDefault();
                    count('submits');
                });
                document.querySelector('[role="textbox"]').addEventListener(
                    'keydown', event => { if (event.key === 'Enter') count('enters'); }
                );
            }"""
        )
        sender = _sender(page)
        owner = await sender._resolve_message_owner(
            _message_target(), expected_route=page.url
        )
        assert owner is not None
        return sender, owner

    @staticmethod
    async def _counters(page) -> dict:
        return await page.evaluate(
            """() => ({
                clicks: document.body.dataset.clicks || '0',
                submits: document.body.dataset.submits || '0',
                enters: document.body.dataset.enters || '0',
            })"""
        )

    @staticmethod
    async def _editor_html(page) -> str:
        return await page.locator('[role="textbox"]').evaluate("e => e.innerHTML")

    async def test_two_lines_are_written_and_clicked_once(self, dom_page):
        message = "Line one\nLine two"
        sender, owner = await self._open(dom_page)

        written = await sender._write_verified_message(
            message, target=_message_target(), owner=owner
        )
        ready = await sender._wait_for_verified_submit(
            message, target=_message_target(), owner=owner
        )
        before = await self._counters(dom_page)
        html = await self._editor_html(dom_page)
        submitted = await sender._submit_verified_message(
            message, target=_message_target(), owner=owner
        )
        after = await self._counters(dom_page)
        await sender._dispose_message_owner(owner)

        assert html == "<p>Line one</p><p>Line two</p>"
        assert (written, ready, submitted) == ("written", True, "clicked")
        assert before == {"clicks": "0", "submits": "0", "enters": "0"}
        assert after == {"clicks": "1", "submits": "0", "enters": "0"}

    @pytest.mark.parametrize("message", ["a\n\nb", "a\n\n\nb"])
    async def test_empty_lines_are_written_and_clicked(self, dom_page, message):
        sender, owner = await self._open(dom_page)

        written = await sender._write_verified_message(
            message, target=_message_target(), owner=owner
        )
        submitted = await sender._submit_verified_message(
            message, target=_message_target(), owner=owner
        )
        await sender._dispose_message_owner(owner)

        assert (written, submitted) == ("written", "clicked")
        assert (await self._counters(dom_page))["clicks"] == "1"

    async def test_space_stored_as_nbsp_still_belongs_to_the_message(self, dom_page):
        message = "line \nnext"
        sender, owner = await self._open(dom_page)

        written = await sender._write_verified_message(
            message, target=_message_target(), owner=owner
        )
        html = await self._editor_html(dom_page)
        await sender._dispose_message_owner(owner)

        assert html == "<p>line&nbsp;</p><p>next</p>"
        assert written == "written"

    @pytest.mark.parametrize(
        "change",
        [
            "editor.insertAdjacentHTML('beforeend', '<p>Author draft</p>')",
            "editor.firstChild.replaceChildren("
            "Object.assign(document.createElement('span'), {textContent: 'Line one'}))",
            "editor.innerHTML = '<p>Line one<br>Line two</p>'",
            "editor.firstChild.replaceWith("
            "Object.assign(document.createElement('div'), {textContent: 'Line one'}))",
            "editor.append(document.createComment('note'))",
            "editor.firstChild.replaceWith('Line one')",
            "editor.firstChild.setAttribute('dir', 'ltr')",
        ],
        ids=[
            "appended-paragraph",
            "line-in-span",
            "br-joined-lines",
            "root-div",
            "root-comment",
            "root-text",
            "paragraph-attribute",
        ],
    )
    async def test_foreign_content_after_write_is_never_sent_or_cleared(
        self, dom_page, change
    ):
        message = "Line one\nLine two"
        sender, owner = await self._open(dom_page)
        written = await sender._write_verified_message(
            message, target=_message_target(), owner=owner
        )
        await dom_page.locator('[role="textbox"]').evaluate(
            f"editor => {{ {change}; }}"
        )
        changed = await self._editor_html(dom_page)

        ready = await sender._wait_for_verified_submit(
            message, target=_message_target(), owner=owner
        )
        submitted = await sender._submit_verified_message(
            message, target=_message_target(), owner=owner
        )
        await sender._cleanup_owned_message(message, owner)
        await sender._dispose_message_owner(owner)

        assert written == "written"
        assert (ready, submitted) == (False, "invalid")
        assert (await self._counters(dom_page))["clicks"] == "0"
        assert await self._editor_html(dom_page) == changed

    async def test_removed_empty_line_is_never_sent(self, dom_page):
        message = "a\n\nb"
        sender, owner = await self._open(dom_page)
        written = await sender._write_verified_message(
            message, target=_message_target(), owner=owner
        )
        await dom_page.locator('[role="textbox"] p:nth-child(2)').evaluate(
            "paragraph => paragraph.remove()"
        )

        submitted = await sender._submit_verified_message(
            message, target=_message_target(), owner=owner
        )
        await sender._dispose_message_owner(owner)

        assert (written, submitted) == ("written", "invalid")
        assert (await self._counters(dom_page))["clicks"] == "0"
        assert await self._editor_html(dom_page) == "<p>a</p><p>b</p>"

    async def test_cleanup_empties_an_owned_multi_line_message(self, dom_page):
        message = "Line one\n\nLine two"
        sender, owner = await self._open(dom_page)
        written = await sender._write_verified_message(
            message, target=_message_target(), owner=owner
        )

        await sender._cleanup_owned_message(message, owner)
        await sender._dispose_message_owner(owner)

        assert written == "written"
        assert await self._editor_html(dom_page) == ""
        assert (await self._counters(dom_page))["clicks"] == "0"
