"""Messaging tools for Upwork MCP."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from pydantic import Field, field_validator

from ..browser.client import get_browser
from .proposals import StrictToolModel, approval_gate, validate_upwork_url


def _validate_room_id(value: str) -> str:
    candidate = value.strip()
    if candidate.startswith("http"):
        url = validate_upwork_url(candidate)
        if "/messages" not in url:
            raise ValueError("Room URL must point to an Upwork messages route")
        return url
    if not re.fullmatch(r"[A-Za-z0-9_-]+", candidate):
        raise ValueError("Room ID may contain only letters, numbers, underscores, and hyphens")
    return candidate


def _room_url(room_id: str) -> str:
    return room_id if room_id.startswith("http") else f"https://www.upwork.com/nx/messages/{room_id}"


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

    room_id: str = Field(description="Chat room ID or URL")
    message: str = Field(min_length=1, max_length=10000, description="Exact message content to send")
    approved: bool = False
    approval_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    action_id: str | None = Field(default=None, min_length=1, max_length=128)

    _validate_room_id_field = field_validator("room_id")(_validate_room_id)

    @field_validator("message")
    @classmethod
    def _message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message cannot be blank")
        return value


def message_payload(params: SendMessageParams) -> dict[str, str]:
    return {"room_id": params.room_id, "message": params.message}


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
    if room_link:
        href = await room_link.get_attribute("href")
        if href:
            conv["room_url"] = href if href.startswith("http") else f"https://www.upwork.com{href}"
            # Extract room ID from URL
            if "/messages/" in href:
                conv["room_id"] = href.split("/messages/")[-1].split("/")[0].split("?")[0]

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

    await page.goto(url, wait_until="networkidle")

    conversation: dict[str, Any] = {
        "room_id": room_id,
        "messages": [],
        "history_complete": False,
        "completeness_note": "Upwork exposes a virtualised message history; this returns the latest visible messages only.",
    }

    # Contact name
    contact_el = await page.query_selector('[data-test="contact-name"], .contact-name, h2')
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


def _normalise_visible_message(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


async def _matching_own_message_count(page, message: str) -> int:
    expected = _normalise_visible_message(message)
    count = 0
    message_els = await page.query_selector_all('[data-test="message"], .message-item, .chat-message')
    for element in message_els:
        try:
            extracted = await _extract_message(element)
        except Exception:
            continue
        if (
            extracted
            and extracted.get("is_mine")
            and _normalise_visible_message(str(extracted.get("content") or "")) == expected
        ):
            count += 1
    return count


async def _last_visible_message(page) -> dict[str, Any] | None:
    message_els = await page.query_selector_all('[data-test="message"], .message-item, .chat-message')
    for element in reversed(message_els):
        try:
            extracted = await _extract_message(element)
        except Exception:
            continue
        if extracted:
            return extracted
    return None


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

    # Navigate to conversation
    url = _room_url(params.room_id)

    await page.goto(url, wait_until="networkidle")

    last_message = await _last_visible_message(page)
    if (
        last_message
        and last_message.get("is_mine")
        and _normalise_visible_message(str(last_message.get("content") or ""))
        == _normalise_visible_message(params.message)
    ):
        return {
            "status": "duplicate_blocked",
            "message": "The latest owner-system message already matches this exact payload.",
            "owner_system_readback": {"confirmed": True, "existing_message": last_message},
            "external_action_taken": False,
        }

    matching_before = await _matching_own_message_count(page, params.message)

    # Find message input
    input_el = await page.query_selector('[data-test="message-input"], textarea[name*="message"], .message-input textarea')
    if not input_el:
        return {"status": "error", "message": "Message input not found", "external_action_taken": False}

    # Type message
    await input_el.fill(params.message)

    # Find and click send button
    send_btn = await page.query_selector('[data-test="send-button"], button[type="submit"]:has-text("Send"), button:has-text("Send")')
    if not send_btn:
        # Try pressing Enter
        await input_el.press("Enter")
    else:
        await send_btn.click()

    # Confirm a *new* matching owner message. A cleared input alone is not proof,
    # and counting prevents a retry from mistaking an older identical message for
    # the write performed by this call.
    for _ in range(20):
        matching_after = await _matching_own_message_count(page, params.message)
        if matching_after > matching_before:
            return {
                "status": "sent",
                "message": "Message sent and read back from Upwork",
                "owner_system_readback": {
                    "confirmed": True,
                    "exact_visible_copy": True,
                    "matching_messages_before": matching_before,
                    "matching_messages_after": matching_after,
                    "room_url": str(getattr(page, "url", url)),
                },
                "external_action_taken": True,
            }
        await asyncio.sleep(0.5)

    input_value = await input_el.input_value()
    return {
        "status": "unknown",
        "message": "Upwork did not show a new exact-copy message; do not retry automatically.",
        "owner_system_readback": {
            "confirmed": False,
            "input_cleared": not bool(input_value),
            "matching_messages_before": matching_before,
            "matching_messages_after": await _matching_own_message_count(page, params.message),
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
