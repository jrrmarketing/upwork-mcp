"""Messaging tools for Upwork MCP."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator

from ..browser.client import get_browser
from ..prepared_actions import prepare_action
from ..strategy import validate_upwork_copy
from .proposals import StrictToolModel, approval_gate, validate_upwork_url

_MESSAGE_ROOM_PATHS = (
    re.compile(r"^/nx/messages/(?P<room_id>[A-Za-z0-9_-]{12,128})/?$"),
    re.compile(r"^/ab/messages/rooms/(?P<room_id>[A-Za-z0-9_-]{12,128})/?$"),
)

_ROOM_CONTACT_SELECTOR = (
    '[data-test="room-header"] [data-test="contact-name"], '
    '[data-test="room-header"] h2, '
    '.room-header .contact-name, '
    '.room-header h2, '
    'header [data-test="contact-name"]'
)
_MESSAGE_RECORD_SELECTOR = '[data-test="message"], .message-item, .chat-message'
_MESSAGE_CONTENT_SELECTORS = (
    '[data-test="content"]',
    '.message-text',
    '.content',
    'p',
)
_OWN_MESSAGE_SELECTOR = '.my-message, [data-test="my-message"], .sent'
_COMPOSER_SELECTOR = (
    'form[data-test="message-composer"], '
    'form[data-test="composer"], '
    'form:has([data-test="message-input"]), '
    'form:has(textarea[name*="message"]), '
    '[data-test="message-composer"], '
    '.message-composer'
)
_COMPOSER_INPUT_SELECTOR = (
    'textarea[data-test="message-input"], '
    'textarea[name*="message"], '
    '[contenteditable="true"][data-test="message-input"]'
)
_SCOPED_SEND_CANDIDATE_SELECTOR = (
    '[data-test="send-button"], '
    '[data-test="send-message-button"], '
    'button[type="submit"], '
    'button[aria-label="Send"], '
    'button[aria-label="Send message"]'
)


def parse_message_room(value: str) -> tuple[str, str]:
    """Return a canonical individual room URL and its conversation ID."""

    candidate = value.strip()
    if "://" in candidate:
        url = validate_upwork_url(candidate)
        parsed = urlparse(url)
        if "?" in url or "#" in url or parsed.query or parsed.fragment:
            raise ValueError("Room URL must not contain a query string or fragment")
        path = parsed.path
        for pattern in _MESSAGE_ROOM_PATHS:
            match = pattern.fullmatch(path)
            if match:
                room_id = match.group("room_id")
                route = (
                    f"/nx/messages/{room_id}"
                    if path.startswith("/nx/")
                    else f"/ab/messages/rooms/{room_id}"
                )
                return f"https://www.upwork.com{route}", room_id
        raise ValueError(
            "Room URL must point to one room at /nx/messages/<id> or /ab/messages/rooms/<id>"
        )
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,128}", candidate):
        raise ValueError("Room ID may contain only letters, numbers, underscores, and hyphens")
    return f"https://www.upwork.com/nx/messages/{candidate}", candidate


def _validate_room_id(value: str) -> str:
    return parse_message_room(value)[1]


def _validate_room_url(value: str) -> str:
    return parse_message_room(value)[0]


def _room_url(room_id: str) -> str:
    return parse_message_room(room_id)[0]


class MessagesParams(StrictToolModel):
    """Parameters for getting messages."""
    room_id: str | None = Field(default=None, description="Specific chat room ID or URL")
    unread_only: bool = Field(default=False, description="Only show unread messages")
    limit: int = Field(default=20, ge=1, le=50, description="Maximum conversations to return")

    @field_validator("room_id")
    @classmethod
    def _validate_optional_room_id(cls, value: str | None) -> str | None:
        return _validate_room_id(value) if value is not None else None


class SendMessageParams(StrictToolModel):
    """Exact approved payload for sending a message."""

    room_url: str = Field(description="Canonical individual Upwork room URL")
    room_id: str = Field(description="Exact room/conversation ID")
    contact_name: str = Field(min_length=1, max_length=500, description="Live recipient identity")
    message: str = Field(min_length=1, max_length=10000, description="Exact message content to send")
    approved: bool = False
    approval_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    action_id: str | None = Field(default=None, min_length=1, max_length=128)

    _validate_room_url_field = field_validator("room_url")(_validate_room_url)

    @field_validator("room_id")
    @classmethod
    def _validate_bound_room_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{12,128}", value):
            raise ValueError("Room ID may contain only letters, numbers, underscores, and hyphens")
        return value

    @field_validator("contact_name")
    @classmethod
    def _normalise_contact_name(cls, value: str) -> str:
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized:
            raise ValueError("Contact name cannot be blank")
        return normalized

    @field_validator("message")
    @classmethod
    def _message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message cannot be blank")
        return value

    @model_validator(mode="after")
    def _room_id_must_match_url(self) -> SendMessageParams:
        if parse_message_room(self.room_url)[1] != self.room_id:
            raise ValueError("room_id does not match the individual messages URL")
        return self


def message_payload(params: SendMessageParams) -> dict[str, str]:
    return {
        "room_url": params.room_url,
        "room_id": params.room_id,
        "contact_name": params.contact_name,
        "message": params.message,
    }


async def get_messages(params: MessagesParams) -> list[dict]:
    """Get messages from Upwork inbox.

    Returns a list of conversations with last message, sender info, and unread status.
    """
    if params.room_id:
        return [await get_conversation_messages(params.room_id, params.limit)]

    browser = get_browser()
    await browser.ensure_logged_in()
    async with browser.operation() as page:
        return await _get_messages_on_page(params, page)


async def _get_messages_on_page(params: MessagesParams, page) -> list[dict]:
    """Read the inbox list while the browser operation lock is held."""

    # Navigate to messages
    url = "https://www.upwork.com/nx/messages"
    if params.unread_only:
        url += "?filter=unread"

    await page.goto(url, wait_until="networkidle")

    conversations = []

    # Wait for message list
    try:
        await page.wait_for_selector('[data-test="room-list"], .room-list, .message-list', timeout=10000)
    except Exception:
        pass

    # Extract conversation items
    room_els = await page.query_selector_all('[data-test="room-item"], .room-item, .conversation-item')

    for el in room_els[:params.limit]:
        try:
            conv = await _extract_conversation(el)
            if conv:
                conversations.append(conv)
        except Exception:
            continue

    return conversations


async def _extract_conversation(el) -> dict | None:
    """Extract conversation data from element."""
    conv = {}

    # Contact name
    name_el = await el.query_selector('[data-test="contact-name"], .contact-name, .sender-name')
    if name_el:
        conv["contact_name"] = (await name_el.text_content() or "").strip()

    if not conv.get("contact_name"):
        return None

    # Room URL/ID
    room_link = await el.query_selector('a[href*="/messages/"]')
    if not room_link:
        return None
    href = await room_link.get_attribute("href")
    if not href:
        return None
    absolute_url = href if "://" in href else f"https://www.upwork.com{href}"
    try:
        room_url, room_id = parse_message_room(absolute_url)
    except ValueError:
        return None
    conv["room_url"] = room_url
    conv["room_id"] = room_id

    # Last message preview
    preview_el = await el.query_selector('[data-test="message-preview"], .preview, .last-message')
    if preview_el:
        conv["last_message"] = (await preview_el.text_content() or "").strip()

    # Timestamp
    time_el = await el.query_selector('[data-test="timestamp"], time, .time')
    if time_el:
        conv["timestamp"] = (await time_el.text_content() or "").strip()

    # Unread indicator
    unread_el = await el.query_selector('[data-test="unread"], .unread-badge, .unread-indicator')
    conv["unread"] = unread_el is not None

    # Related job (if any)
    job_el = await el.query_selector('[data-test="related-job"], .job-title')
    if job_el:
        conv["related_job"] = (await job_el.text_content() or "").strip()

    return conv


async def get_conversation_messages(room_id: str, limit: int = 50) -> dict:
    """Get all messages in a specific conversation.

    Args:
        room_id: The room ID or URL
        limit: Maximum messages to return

    Returns conversation details with full message history.
    """
    room_id = _validate_room_id(room_id)
    browser = get_browser()
    await browser.ensure_logged_in()
    async with browser.operation() as page:
        return await _get_conversation_on_page(room_id, limit, page)


async def _get_conversation_on_page(room_id: str, limit: int, page) -> dict:
    """Read one conversation while the browser operation lock is held."""

    # Build URL
    url = _room_url(room_id)
    expected_room_id = parse_message_room(room_id)[1]

    await page.goto(url, wait_until="networkidle")

    live_url, live_room_id = parse_message_room(str(getattr(page, "url", "")))
    if live_room_id != expected_room_id:
        raise ValueError("Upwork opened a different message room than requested")

    conversation: dict[str, Any] = {
        "room_url": live_url,
        "room_id": live_room_id,
        "messages": [],
        "history_complete": False,
        "completeness_note": "Upwork exposes a virtualised message history; this returns the latest visible messages only.",
    }

    # Contact name
    contact_el = await page.query_selector(
        '[data-test="contact-name"], .contact-name, .sender-name, '
        '[data-test="room-header"] h2, .room-header h2'
    )
    if contact_el:
        conversation["contact_name"] = (await contact_el.text_content() or "").strip()

    # Related job
    job_el = await page.query_selector('[data-test="related-job"], .job-link')
    if job_el:
        conversation["related_job"] = (await job_el.text_content() or "").strip()

    # Extract messages
    message_els = await page.query_selector_all('[data-test="message"], .message-item, .chat-message')

    for el in message_els[-limit:]:  # Get last N messages
        try:
            msg = await _extract_message(el)
            if msg:
                conversation["messages"].append(msg)
        except Exception:
            continue

    return conversation


def _conversation_identity(conversation: dict[str, Any]) -> dict[str, str] | None:
    room_url = str(conversation.get("room_url") or "").strip()
    room_id = str(conversation.get("room_id") or "").strip()
    contact_name = re.sub(r"\s+", " ", str(conversation.get("contact_name") or "")).strip()
    if not room_url or not room_id or not contact_name:
        return None
    try:
        canonical_url, route_id = parse_message_room(room_url)
    except ValueError:
        return None
    if route_id != room_id:
        return None
    return {
        "room_url": canonical_url,
        "room_id": room_id,
        "contact_name": contact_name,
    }


async def prepare_message_from_live(room: str, message: str) -> dict[str, Any]:
    """Read one exact room and bind its recipient before creating approval state."""

    canonical_url, room_id = parse_message_room(room)
    browser = get_browser()
    await browser.ensure_logged_in()
    async with browser.operation() as page:
        conversation = await _get_conversation_on_page(canonical_url, 1, page)
    identity = _conversation_identity(conversation)
    validation = validate_upwork_copy(message)
    errors = list(validation["errors"])
    if identity is None:
        errors.append("The message room and recipient identity could not be read back from Upwork")
    elif identity["room_id"] != room_id:
        errors.append("Upwork returned a different message room than requested")

    payload: dict[str, str] | None = None
    prepared = None
    if identity is not None:
        params = SendMessageParams(
            room_url=identity["room_url"],
            room_id=identity["room_id"],
            contact_name=identity["contact_name"],
            message=message,
        )
        payload = message_payload(params)
        if not errors:
            prepared = prepare_action("message", payload)

    return {
        **validation,
        "valid": not errors,
        "errors": list(dict.fromkeys(errors)),
        "current_conversation": identity,
        "exact_message": payload,
        "prepared_action": prepared,
        "external_action_taken": False,
        "next_step": "Show the exact message and recipient to Josiah, then wait for approval",
    }


async def _extract_message(el) -> dict | None:
    """Extract message data from element."""
    msg = {}

    # Sender
    sender_el = await el.query_selector('[data-test="sender"], .sender, .author')
    if sender_el:
        msg["sender"] = (await sender_el.text_content() or "").strip()

    # Message content
    content_el = await el.query_selector('[data-test="content"], .content, .message-text, p')
    if content_el:
        msg["content"] = (await content_el.text_content() or "").strip()

    if not msg.get("content"):
        return None

    # Timestamp
    time_el = await el.query_selector('[data-test="timestamp"], time, .time')
    if time_el:
        msg["timestamp"] = (await time_el.text_content() or "").strip()

    # Check if it's from me
    me_indicator = await el.query_selector('.my-message, [data-test="my-message"], .sent')
    msg["is_mine"] = me_indicator is not None

    # Attachments
    attachment_els = await el.query_selector_all('[data-test="attachment"], .attachment')
    attachments = []
    for att in attachment_els:
        att_name = await att.text_content()
        if att_name:
            attachments.append(att_name.strip())
    if attachments:
        msg["attachments"] = attachments

    return msg


async def _visible_elements(scope, selector: str) -> list[Any] | None:
    """Enumerate every visible matching element, or fail closed on unreadable DOM."""

    try:
        candidates = await scope.query_selector_all(selector)
    except Exception:
        return None
    visible = []
    for candidate in candidates:
        try:
            if await candidate.is_visible():
                visible.append(candidate)
        except Exception:
            return None
    return visible


async def _current_conversation_identity(page) -> tuple[dict[str, str] | None, str | None]:
    """Read one exact room route and one visible room-header contact."""

    try:
        room_url, room_id = parse_message_room(str(getattr(page, "url", "")))
    except (TypeError, ValueError):
        return None, "The current page is not one exact supported Upwork message room"

    contacts = await _visible_elements(page, _ROOM_CONTACT_SELECTOR)
    if contacts is None:
        return None, "The room-header contact controls could not be completely read"
    if len(contacts) != 1:
        return None, "Exactly one visible room-header contact was not found"
    try:
        contact_name = re.sub(r"\s+", " ", (await contacts[0].text_content() or "")).strip()
    except Exception:
        return None, "The room-header contact name could not be read"
    if not contact_name:
        return None, "The room-header contact name was blank"
    return {
        "room_url": room_url,
        "room_id": room_id,
        "contact_name": contact_name,
    }, None


async def _strict_message_content(element) -> tuple[str | None, str | None]:
    """Read one complete message body without silently choosing among body nodes."""

    for selector in _MESSAGE_CONTENT_SELECTORS:
        contents = await _visible_elements(element, selector)
        if contents is None:
            return None, "A visible message body could not be completely enumerated"
        if len(contents) > 1:
            return None, "A visible message record exposed multiple ambiguous body nodes"
        if len(contents) == 1:
            try:
                content = await contents[0].text_content()
            except Exception:
                return None, "A visible message body could not be read"
            if content is None:
                return None, "A visible message body returned no readable content"
            return content, None
    return None, "A visible message record did not expose a readable body"


async def _strict_message_record(element) -> tuple[dict[str, Any] | None, str | None]:
    """Read the exact body and ownership of one currently rendered message record."""

    content, error = await _strict_message_content(element)
    if error:
        return None, error
    try:
        mine_indicator = await element.query_selector(_OWN_MESSAGE_SELECTOR)
        data_test = (await element.get_attribute("data-test") or "").strip().casefold()
        class_names = (await element.get_attribute("class") or "").split()
    except Exception:
        return None, "A visible message record's ownership could not be read"
    is_mine = (
        mine_indicator is not None
        or data_test == "my-message"
        or any(name.casefold() in {"my-message", "sent"} for name in class_names)
    )
    return {"content": content, "is_mine": is_mine}, None


async def _visible_message_history(page) -> dict[str, Any]:
    """Read every currently rendered message record without skipping failures."""

    elements = await _visible_elements(page, _MESSAGE_RECORD_SELECTOR)
    if elements is None:
        return {
            "status": "unavailable",
            "messages": [],
            "message": "The visible message-history records could not be enumerated",
        }
    records: list[dict[str, Any]] = []
    for index, element in enumerate(elements):
        record, error = await _strict_message_record(element)
        if error or record is None:
            return {
                "status": "incomplete",
                "messages": records,
                "rendered_record_count": len(elements),
                "unreadable_record_index": index,
                "message": error or "A visible message record could not be read",
            }
        records.append(record)
    return {
        "status": "complete",
        "messages": records,
        "rendered_record_count": len(elements),
        "message": "Every currently rendered message record was read",
    }


def _exact_own_message_count(history: dict[str, Any], message: str) -> int:
    return sum(
        1
        for record in history.get("messages", [])
        if record.get("is_mine") is True and record.get("content") == message
    )


def _exact_message_matches(history: dict[str, Any], message: str) -> list[dict[str, Any]]:
    return [
        record
        for record in history.get("messages", [])
        if record.get("is_mine") is True and record.get("content") == message
    ]


async def _read_composer_value(input_element) -> str | None:
    try:
        return await input_element.input_value()
    except Exception:
        try:
            contenteditable = (await input_element.get_attribute("contenteditable") or "")
            if contenteditable.casefold() != "true":
                return None
            return await input_element.text_content()
        except Exception:
            return None


def _same_utf8_bytes(left: str, right: str) -> bool:
    try:
        return left.encode("utf-8") == right.encode("utf-8")
    except UnicodeEncodeError:
        return False


def _same_complete_visible_history(
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> bool:
    """Compare complete rendered histories without normalising message bodies."""

    if baseline.get("status") != "complete" or current.get("status") != "complete":
        return False
    if baseline.get("rendered_record_count") != current.get("rendered_record_count"):
        return False
    baseline_messages = baseline.get("messages")
    current_messages = current.get("messages")
    if not isinstance(baseline_messages, list) or not isinstance(current_messages, list):
        return False
    if len(baseline_messages) != len(current_messages):
        return False
    for baseline_record, current_record in zip(baseline_messages, current_messages, strict=True):
        if not isinstance(baseline_record, dict) or not isinstance(current_record, dict):
            return False
        baseline_content = baseline_record.get("content")
        current_content = current_record.get("content")
        if not isinstance(baseline_content, str) or not isinstance(current_content, str):
            return False
        if not _same_utf8_bytes(baseline_content, current_content):
            return False
        baseline_is_mine = baseline_record.get("is_mine")
        current_is_mine = current_record.get("is_mine")
        if type(baseline_is_mine) is not bool or type(current_is_mine) is not bool:
            return False
        if baseline_is_mine is not current_is_mine:
            return False
    return True


async def _restore_composer_value(input_element, original_value: str) -> bool:
    """Restore and exactly verify a composer after a pre-click failure."""

    try:
        await input_element.fill(original_value)
    except Exception:
        return False
    restored = await _read_composer_value(input_element)
    return restored is not None and _same_utf8_bytes(restored, original_value)


async def _preclick_failure(
    input_element,
    *,
    status: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Restore the original blank composer or report unknown autosaved draft state."""

    restored = await _restore_composer_value(input_element, "")
    if not restored:
        return {
            "status": "draft_state_unknown",
            "message": (
                "The message was not intentionally sent, but the original blank composer could "
                "not be restored and verified. Upwork may have autosaved a draft; inspect the "
                "exact room and do not retry automatically."
            ),
            "preclick_failure_status": status,
            "preclick_failure_message": message,
            **(details or {}),
            "composer_restored": False,
            "external_action_taken": True,
        }
    return {
        "status": status,
        "message": message,
        **(details or {}),
        "composer_restored": True,
        "external_action_taken": False,
    }


async def _resolve_exact_composer(page) -> tuple[Any | None, Any | None, str | None]:
    """Resolve one visible composer and one writable field scoped inside it."""

    composers = await _visible_elements(page, _COMPOSER_SELECTOR)
    if composers is None:
        return None, None, "The message composers could not be completely enumerated"
    if len(composers) != 1:
        return None, None, "Exactly one visible message composer was not found"
    inputs = await _visible_elements(composers[0], _COMPOSER_INPUT_SELECTOR)
    if inputs is None:
        return None, None, "The selected composer's writable fields could not be enumerated"
    if len(inputs) != 1:
        return None, None, "Exactly one visible message field was not found in the composer"
    return composers[0], inputs[0], None


async def _resolve_exact_scoped_send(composer) -> tuple[Any | None, str | None]:
    """Resolve one exact Send control inside the already-bound composer."""

    candidates = await _visible_elements(composer, _SCOPED_SEND_CANDIDATE_SELECTOR)
    if candidates is None:
        return None, "The composer's Send controls could not be completely enumerated"
    exact = []
    for candidate in candidates:
        try:
            data_test = (await candidate.get_attribute("data-test") or "").casefold()
            aria_label = (await candidate.get_attribute("aria-label") or "").strip().casefold()
            control_type = (await candidate.get_attribute("type") or "").casefold()
            text = re.sub(r"\s+", " ", (await candidate.text_content() or "")).strip().casefold()
        except Exception:
            return None, "A composer-scoped Send candidate could not be completely read"
        if (
            data_test in {"send-button", "send-message-button"}
            or aria_label in {"send", "send message"}
            or (control_type == "submit" and text in {"send", "send message"})
        ):
            exact.append(candidate)
    if len(exact) != 1:
        return None, "Exactly one exact composer-scoped Send control was not found"
    try:
        if not await exact[0].is_enabled():
            return None, "The exact composer-scoped Send control was disabled"
    except Exception:
        return None, "The exact composer-scoped Send control state could not be read"
    return exact[0], None


async def send_message(params: SendMessageParams) -> dict:
    """Send a message in a conversation.

    Args:
        params.room_id: Chat room ID or URL
        params.message: Message content

    Returns send status.
    """
    payload = message_payload(params)
    blocked = approval_gate(
        "send_message",
        payload,
        approved=params.approved,
        approval_sha256=params.approval_sha256,
        action_id=params.action_id,
    )
    if blocked:
        return blocked

    browser = get_browser()
    await browser.ensure_logged_in()
    async with browser.operation() as page:
        return await _send_message_on_page(params, page)


async def _send_message_on_page(params: SendMessageParams, page) -> dict:
    """Send while the browser operation lock is held."""

    url = params.room_url
    try:
        await page.goto(url, wait_until="networkidle")
    except Exception:
        return {
            "status": "error",
            "message": "The exact approved message room could not be opened",
            "external_action_taken": False,
        }

    live_identity, identity_error = await _current_conversation_identity(page)
    approved_identity = {
        "room_url": params.room_url,
        "room_id": params.room_id,
        "contact_name": re.sub(r"\s+", " ", params.contact_name).strip(),
    }
    if live_identity != approved_identity:
        return {
            "status": "live_identity_mismatch",
            "message": (
                "The live conversation or recipient differs from the approved payload; "
                "nothing was sent. Prepare the current room again before new approval."
            ),
            "identity_read_error": identity_error,
            "approved_conversation_identity": approved_identity,
            "live_conversation_identity": live_identity,
            "external_action_taken": False,
        }

    history_before = await _visible_message_history(page)
    if history_before["status"] != "complete":
        return {
            "status": "history_unreadable",
            "message": (
                "The complete currently rendered message history could not be read; "
                "nothing was sent."
            ),
            "visible_history_readback": history_before,
            "external_action_taken": False,
        }

    existing_matches = _exact_message_matches(history_before, params.message)
    if existing_matches:
        return {
            "status": "duplicate_blocked",
            "message": (
                "A message anywhere in the currently rendered owner-system history already "
                "matches this exact payload."
            ),
            "owner_system_readback": {
                "confirmed": True,
                "conversation_identity": approved_identity,
                "visible_history_complete": True,
                "rendered_record_count": history_before["rendered_record_count"],
                "existing_message": existing_matches[0],
            },
            "external_action_taken": False,
        }

    matching_before = _exact_own_message_count(history_before, params.message)

    composer, input_el, composer_error = await _resolve_exact_composer(page)
    if composer_error or composer is None or input_el is None:
        return {
            "status": "composer_unavailable",
            "message": composer_error or "The exact message composer was unavailable",
            "external_action_taken": False,
        }
    initial_value = await _read_composer_value(input_el)
    if initial_value is None:
        return {
            "status": "composer_unreadable",
            "message": "The exact composer's current value could not be read",
            "external_action_taken": False,
        }
    if initial_value:
        return {
            "status": "draft_present",
            "message": "The exact composer already contains a draft; nothing was overwritten or sent.",
            "external_action_taken": False,
        }
    try:
        await input_el.fill(params.message)
    except Exception:
        return await _preclick_failure(
            input_el,
            status="composer_unreadable",
            message="The exact approved copy could not be filled into the bound composer",
        )
    composer_readback = await _read_composer_value(input_el)
    if composer_readback is None or not _same_utf8_bytes(composer_readback, params.message):
        return await _preclick_failure(
            input_el,
            status="composer_readback_mismatch",
            message="The bound composer did not read back the approved copy byte-for-byte",
        )

    rebound_identity, rebound_error = await _current_conversation_identity(page)
    if rebound_identity != approved_identity:
        return await _preclick_failure(
            input_el,
            status="live_identity_mismatch",
            message=(
                "The live conversation or recipient changed after the composer was filled; "
                "nothing was sent."
            ),
            details={
                "identity_read_error": rebound_error,
                "approved_conversation_identity": approved_identity,
                "live_conversation_identity": rebound_identity,
            },
        )

    send_btn, send_error = await _resolve_exact_scoped_send(composer)
    if send_error or send_btn is None:
        return await _preclick_failure(
            input_el,
            status="send_control_unavailable",
            message=send_error or "The exact composer-scoped Send control was unavailable",
        )

    # Resolving the action control yields to the live page. Rebind every approved
    # target and mutable input immediately before the irreversible click so a room
    # switch or newly rendered inbound/outbound message cannot race the approval.
    final_identity, final_identity_error = await _current_conversation_identity(page)
    if final_identity != approved_identity:
        return await _preclick_failure(
            input_el,
            status="live_identity_mismatch",
            message=(
                "The live conversation or recipient changed while the Send control was "
                "resolved; nothing was sent."
            ),
            details={
                "identity_read_error": final_identity_error,
                "approved_conversation_identity": approved_identity,
                "live_conversation_identity": final_identity,
            },
        )

    final_history = await _visible_message_history(page)
    if final_history["status"] != "complete":
        return await _preclick_failure(
            input_el,
            status="history_unreadable",
            message=(
                "The complete visible message history could not be re-read immediately before "
                "Send; nothing was sent."
            ),
            details={"visible_history_readback": final_history},
        )
    if not _same_complete_visible_history(history_before, final_history):
        return await _preclick_failure(
            input_el,
            status="message_history_changed",
            message=(
                "The visible message history changed after the composer was filled; nothing was "
                "sent. Review the new activity and prepare the message again."
            ),
            details={
                "baseline_rendered_record_count": history_before["rendered_record_count"],
                "current_rendered_record_count": final_history["rendered_record_count"],
            },
        )

    final_composer_readback = await _read_composer_value(input_el)
    if (
        final_composer_readback is None
        or not _same_utf8_bytes(final_composer_readback, params.message)
    ):
        return await _preclick_failure(
            input_el,
            status="composer_readback_mismatch",
            message="The approved copy changed before the exact Send control could be clicked",
        )
    try:
        await send_btn.click()
    except Exception:
        return {
            "status": "unknown",
            "message": (
                "The exact Send click did not complete cleanly; inspect Upwork and do not retry "
                "automatically."
            ),
            "owner_system_readback": {
                "confirmed": False,
                "conversation_identity": approved_identity,
                "room_url": str(getattr(page, "url", url)),
            },
            "external_action_taken": True,
        }

    # Confirm a *new* matching owner message. A cleared input alone is not proof,
    # and the complete pre-send history prevents an older identical message from
    # being mistaken for the write performed by this call.
    last_history = history_before
    for _ in range(20):
        last_history = await _visible_message_history(page)
        if last_history["status"] != "complete":
            return {
                "status": "unknown",
                "message": (
                    "The Send control was clicked but the currently rendered message history "
                    "could not be completely read; do not retry automatically."
                ),
                "owner_system_readback": {
                    "confirmed": False,
                    "visible_history_readback": last_history,
                    "conversation_identity": approved_identity,
                    "room_url": str(getattr(page, "url", url)),
                },
                "external_action_taken": True,
            }
        matching_after = _exact_own_message_count(last_history, params.message)
        last_record = last_history["messages"][-1] if last_history["messages"] else None
        input_after_send = await _read_composer_value(input_el)
        exact_new_last_record = bool(
            last_record
            and last_record.get("is_mine") is True
            and last_record.get("content") == params.message
        )
        if (
            matching_after > matching_before
            and exact_new_last_record
            and input_after_send == ""
        ):
            readback_identity, readback_error = await _current_conversation_identity(page)
            if readback_identity != approved_identity:
                return {
                    "status": "unknown",
                    "message": (
                        "Upwork showed the new message but the same conversation identity could not "
                        "be read back; do not retry automatically."
                    ),
                    "owner_system_readback": {
                        "confirmed": False,
                        "matching_messages_before": matching_before,
                        "matching_messages_after": matching_after,
                        "conversation_identity": readback_identity,
                        "identity_read_error": readback_error,
                        "room_url": str(getattr(page, "url", url)),
                    },
                    "external_action_taken": True,
                }
            return {
                "status": "sent",
                "message": "Message sent and read back from Upwork",
                "owner_system_readback": {
                    "confirmed": True,
                    "exact_visible_copy": True,
                    "exact_composer_copy_before_send": True,
                    "composer_cleared_after_send": True,
                    "exact_copy_is_last_visible_message": True,
                    "visible_history_complete": True,
                    "rendered_record_count": last_history["rendered_record_count"],
                    "matching_messages_before": matching_before,
                    "matching_messages_after": matching_after,
                    "conversation_identity": readback_identity,
                    "room_url": str(getattr(page, "url", url)),
                },
                "external_action_taken": True,
            }
        await asyncio.sleep(0.5)

    input_value = await _read_composer_value(input_el)
    return {
        "status": "unknown",
        "message": "Upwork did not show a new exact-copy message; do not retry automatically.",
        "owner_system_readback": {
            "confirmed": False,
            "input_cleared": None if input_value is None else not bool(input_value),
            "matching_messages_before": matching_before,
            "matching_messages_after": _exact_own_message_count(last_history, params.message),
            "visible_history_readback": last_history,
            "conversation_identity": approved_identity,
            "room_url": str(getattr(page, "url", url)),
        },
        "external_action_taken": True,
    }


async def get_unread_count() -> dict:
    """Get count of unread messages.

    Returns total unread message count.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    async with browser.operation() as page:
        return await _get_unread_count_on_page(page)


async def _get_unread_count_on_page(page) -> dict:
    """Read the unread badge while the browser operation lock is held."""

    # Check messages badge in header
    await page.goto("https://www.upwork.com/nx/find-work/", wait_until="networkidle")

    unread_el = await page.query_selector('[data-test="messages-badge"], .messages-count, .unread-count')
    if unread_el:
        text = (await unread_el.text_content() or "").strip()
        import re
        numbers = re.findall(r'\d+', text)
        if numbers:
            return {"unread_count": int(numbers[0])}

    return {"unread_count": 0}
