"""Tests for the section contracts every scraping workflow returns."""

from typing import Any

import pytest

from linkedin_mcp_server.scraping import contracts
from linkedin_mcp_server.scraping.contracts import (
    RATE_LIMITED_SECTION_TEXT,
    SEND_INTERRUPTED_WARNING,
    ExtractedSection,
    FilterValidationError,
    message_action_result,
    _normalize_message_line_endings,
    rate_limited_section_error,
    refuse_an_invalid_message,
)


class TestRateLimitedSection:
    def test_the_sentinel_text_is_what_reaches_the_client(self):
        # Pinned as a literal on purpose. Every other assertion in the suite
        # compares a result against this same constant, so it moves with any
        # edit and none of them can see the message a client would read.
        assert RATE_LIMITED_SECTION_TEXT == (
            "[Rate limited] LinkedIn blocked this section. "
            "Try again later or request fewer sections."
        )

    def test_the_reported_error_repeats_the_sentinel_verbatim(self):
        # The tools compare a section's text against the sentinel and then
        # report this error, so the two drifting apart would describe a
        # section the caller never saw.
        assert rate_limited_section_error() == {
            "error_type": "rate_limit",
            "error_message": RATE_LIMITED_SECTION_TEXT,
        }


class TestExtractedSection:
    def test_a_section_without_an_error_carries_none(self):
        section = ExtractedSection(text="Bill Gates", references=[])

        assert section.error is None

    def test_an_error_is_kept_beside_the_text(self):
        section = ExtractedSection(
            text="", references=[], error=rate_limited_section_error()
        )

        assert section.text == ""
        assert section.error == rate_limited_section_error()


class TestFilterValidationError:
    def test_it_is_still_a_value_error(self):
        # Direct extractor callers catch ValueError; the tool wrappers catch
        # this subclass to surface the message past mask_error_details.
        assert issubclass(FilterValidationError, ValueError)


class TestMessageActionResult:
    def test_the_retry_contract_is_explicit_on_every_result(self):
        assert message_action_result(
            "https://www.linkedin.com/messaging/compose/",
            "sent",
            "Message submitted.",
            recipient_selected=True,
            sent=True,
            retry_safe=False,
        ) == {
            "url": "https://www.linkedin.com/messaging/compose/",
            "status": "sent",
            "message": "Message submitted.",
            "recipient_selected": True,
            "sent": True,
            "retry_safe": False,
        }

    def test_the_interruption_warning_names_duplicate_delivery(self):
        assert SEND_INTERRUPTED_WARNING == (
            "Message submission was interrupted while in flight. The send outcome "
            "is unknown; check the conversation before retrying, as a retry may "
            "deliver the message twice."
        )


class TestRefuseAnInvalidMessage:
    @pytest.mark.parametrize("message", ["before\x01after", "text\x7f"])
    def test_unsupported_control_characters_are_refused(self, message: str):
        assert refuse_an_invalid_message("alice", message) == message_action_result(
            "https://www.linkedin.com/in/alice/",
            "invalid_message",
            "Message contains an unsupported control character.",
        )

    def test_whitespace_is_refused_before_normal_message_text(self):
        assert refuse_an_invalid_message("alice", "   ") == message_action_result(
            "https://www.linkedin.com/in/alice/",
            "invalid_message",
            "Message must contain non-whitespace characters.",
        )

    def test_multiline_text_and_tabs_are_accepted(self):
        assert refuse_an_invalid_message("alice", "Hello, Alice!\n\nThanks.") is None
        assert refuse_an_invalid_message("alice", "Column one\tColumn two") is None

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("First\nSecond", "First\nSecond"),
            ("First\rSecond", "First\nSecond"),
            ("First\r\nSecond", "First\nSecond"),
        ],
        ids=["lf", "cr", "crlf"],
    )
    def test_line_endings_are_normalized(self, message: str, expected: str):
        assert _normalize_message_line_endings(message) == expected

    def test_the_refusal_calls_the_owner_constructor_directly(self, monkeypatch):
        calls: list[tuple[str, str, str]] = []
        sentinel: dict[str, Any] = {"owner": "contracts"}

        def constructor(url: str, status: str, message: str) -> dict[str, Any]:
            calls.append((url, status, message))
            return sentinel

        monkeypatch.setattr(contracts, "message_action_result", constructor)

        assert refuse_an_invalid_message("alice", "") is sentinel
        assert calls == [
            (
                "https://www.linkedin.com/in/alice/",
                "invalid_message",
                "Message must contain non-whitespace characters.",
            )
        ]
