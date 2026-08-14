"""Messaging tools for Upwork MCP."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
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
_MESSAGE_HISTORY_CONTAINER_SELECTOR = (
    '[data-test="message-history"], '
    '[data-test="message-list"], '
    '[role="log"][aria-label*="message" i], '
    '.message-list'
)
_ROOM_SCOPE_IDENTITY_ATTRIBUTES = (
    "data-room-id",
    "data-conversation-id",
    "data-room-uid",
)
_MESSAGE_RECORD_SELECTOR = '[data-test="message"], .message-item, .chat-message'
_HISTORY_COMPLETE_BOUNDARY_SELECTOR = (
    '[data-test="message-history-complete"], '
    '[data-test="message-history-start"][data-history-complete="true"], '
    '[data-test="conversation-start"][data-history-complete="true"]'
)
_HISTORY_INCOMPLETE_SELECTOR = (
    '[data-test="load-older-messages"], '
    '[data-test="load-earlier-messages"], '
    'button:text-is("Load older messages"), '
    'button:text-is("Load earlier messages"), '
    '[data-virtualized="true"], '
    '[data-test*="virtualized-message-list"]'
)
_MESSAGE_CONTENT_SELECTORS = (
    '[data-test="content"]',
    '.message-text',
    '.content',
    'p',
)
_OWN_MESSAGE_SELECTOR = '.my-message, [data-test="my-message"], .sent'
_OTHER_MESSAGE_SELECTOR = (
    '.their-message, [data-test="other-message"], [data-test="received-message"], '
    '.received, .incoming'
)
_MESSAGE_IDENTITY_ATTRIBUTES = (
    "data-message-id",
    "data-message-uid",
    "data-test-key",
    "id",
)
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

_MESSAGE_COMMIT_GUARD_SCRIPT = r"""
(args) => {
  const visible = (element) => {
    if (!(element instanceof Element) || !element.isConnected) return false;
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" &&
      rect.width > 0 && rect.height > 0;
  };
  const normalized = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const visibleAll = (scope, selector) =>
    Array.from(scope.querySelectorAll(selector)).filter(visible);
  const exactRoomScope = (scope) => {
    const values = ["data-room-id", "data-conversation-id", "data-room-uid"]
      .map((name) => normalized(scope.getAttribute(name)))
      .filter(Boolean);
    return new Set(values).size === 1 && values[0] === args.roomId;
  };
  const historySelector = '[data-test="message-history"], [data-test="message-list"], ' +
    '[role="log"][aria-label*="message" i], .message-list';
  const composerSelector = 'form[data-test="message-composer"], form[data-test="composer"], ' +
    'form:has([data-test="message-input"]), form:has(textarea[name*="message"]), ' +
    '[data-test="message-composer"], .message-composer';
  const inputSelector = 'textarea[data-test="message-input"], textarea[name*="message"], ' +
    '[contenteditable="true"][data-test="message-input"]';
  const histories = visibleAll(document, historySelector);
  const composers = visibleAll(document, composerSelector);
  if (histories.length !== 1 || !exactRoomScope(histories[0])) {
    return {status: "rejected", message: "The atomic guard could not bind one exact room history"};
  }
  if (composers.length !== 1 || !exactRoomScope(composers[0])) {
    return {status: "rejected", message: "The atomic guard could not bind one exact room composer"};
  }
  if (visibleAll(composers[0], inputSelector).length !== 1) {
    return {status: "rejected", message: "The atomic guard could not bind one exact composer input"};
  }
  const sendSelector = '[data-test="send-button"], [data-test="send-message-button"], ' +
    'button[type="submit"], button[aria-label="Send"], button[aria-label="Send message"]';
  const sendCandidates = visibleAll(composers[0], sendSelector).filter((candidate) => {
    if (!(candidate instanceof HTMLButtonElement) || candidate.disabled ||
        normalized(candidate.getAttribute("aria-disabled")).toLowerCase() === "true") return false;
    const dataTest = normalized(candidate.getAttribute("data-test")).toLowerCase();
    const aria = normalized(candidate.getAttribute("aria-label")).toLowerCase();
    const type = normalized(candidate.getAttribute("type")).toLowerCase();
    const text = normalized(candidate.textContent).toLowerCase();
    const semanticSend = ["send", "send message"].includes(aria) ||
      ["send", "send message"].includes(text);
    const knownSend = ["send-button", "send-message-button"].includes(dataTest) ||
      ["send", "send message"].includes(aria) || type === "submit";
    return semanticSend && knownSend;
  });
  if (sendCandidates.length !== 1) {
    return {status: "rejected", message: "The atomic guard could not bind one exact Send action"};
  }
  const priorHistory = histories[0].__upworkMcpMessageCommitGuard;
  const priorComposer = composers[0].__upworkMcpMessageCommitGuard;
  for (const prior of [priorHistory, priorComposer]) {
    if (prior && prior.observer instanceof MutationObserver) prior.observer.disconnect();
  }
  const actionForm = sendCandidates[0].closest("form");
  const state = {
    token: args.token,
    generation: 0,
    eventGeneration: 0,
    handlerGeneration: 0,
    observer: null,
    inputListener: null,
    changeListener: null,
    actionTarget: sendCandidates[0],
    actionOnClick: sendCandidates[0].onclick,
    formOnSubmit: actionForm ? actionForm.onsubmit : null,
    documentOnClick: document.onclick,
    documentOnSubmit: document.onsubmit,
    originalAddEventListener: EventTarget.prototype.addEventListener,
    originalRemoveEventListener: EventTarget.prototype.removeEventListener,
    addEventListenerWrapper: null,
    removeEventListenerWrapper: null
  };
  const actionEventTarget = (target, type) => {
    if (type !== "click" && type !== "submit") return false;
    return target === window || target === sendCandidates[0] ||
      (target instanceof Node && target.contains(sendCandidates[0]));
  };
  state.addEventListenerWrapper = function(type, listener, options) {
    if (actionEventTarget(this, String(type).toLowerCase())) state.handlerGeneration += 1;
    return state.originalAddEventListener.call(this, type, listener, options);
  };
  state.removeEventListenerWrapper = function(type, listener, options) {
    if (actionEventTarget(this, String(type).toLowerCase())) state.handlerGeneration += 1;
    return state.originalRemoveEventListener.call(this, type, listener, options);
  };
  EventTarget.prototype.addEventListener = state.addEventListenerWrapper;
  EventTarget.prototype.removeEventListener = state.removeEventListenerWrapper;
  if (EventTarget.prototype.addEventListener !== state.addEventListenerWrapper ||
      EventTarget.prototype.removeEventListener !== state.removeEventListenerWrapper) {
    return {status: "rejected", message: "Action-listener mutation tracking could not be installed"};
  }
  state.observer = new MutationObserver((records) => {
    state.generation += records.length;
  });
  state.observer.observe(histories[0], {
    subtree: true, childList: true, characterData: true, attributes: true
  });
  state.observer.observe(composers[0], {
    subtree: true, childList: true, characterData: true, attributes: true
  });
  state.inputListener = () => { state.eventGeneration += 1; };
  state.changeListener = () => { state.eventGeneration += 1; };
  composers[0].addEventListener("input", state.inputListener, true);
  composers[0].addEventListener("change", state.changeListener, true);
  Object.defineProperty(histories[0], "__upworkMcpMessageCommitGuard", {
    value: state, configurable: true
  });
  Object.defineProperty(composers[0], "__upworkMcpMessageCommitGuard", {
    value: state, configurable: true
  });
  return {
    status: "ready",
    generation: state.generation,
    eventGeneration: state.eventGeneration,
    handlerGeneration: state.handlerGeneration
  };
}
"""

_ATOMIC_MESSAGE_COMMIT_SCRIPT = r"""
(args) => {
  const reject = (message) => ({status: "rejected", dispatchStarted: false, message});
  const visible = (element) => {
    if (!(element instanceof Element) || !element.isConnected) return false;
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" &&
      rect.width > 0 && rect.height > 0;
  };
  const normalized = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const visibleAll = (scope, selector) =>
    Array.from(scope.querySelectorAll(selector)).filter(visible);
  const exactRoomScope = (scope) => {
    const values = ["data-room-id", "data-conversation-id", "data-room-uid"]
      .map((name) => normalized(scope.getAttribute(name)))
      .filter(Boolean);
    return new Set(values).size === 1 && values[0] === args.roomId;
  };
  const expectedUrl = new URL(args.roomUrl);
  if (location.origin !== expectedUrl.origin || location.pathname.replace(/\/$/, "") !==
      expectedUrl.pathname.replace(/\/$/, "") || location.search || location.hash) {
    return reject("The exact room route changed at the atomic commit boundary");
  }
  const contactSelector = '[data-test="room-header"] [data-test="contact-name"], ' +
    '[data-test="room-header"] h2, .room-header .contact-name, .room-header h2, ' +
    'header [data-test="contact-name"]';
  const contacts = visibleAll(document, contactSelector);
  if (contacts.length !== 1 || normalized(contacts[0].textContent) !== args.contactName) {
    return reject("The exact recipient changed at the atomic commit boundary");
  }
  const historySelector = '[data-test="message-history"], [data-test="message-list"], ' +
    '[role="log"][aria-label*="message" i], .message-list';
  const composerSelector = 'form[data-test="message-composer"], form[data-test="composer"], ' +
    'form:has([data-test="message-input"]), form:has(textarea[name*="message"]), ' +
    '[data-test="message-composer"], .message-composer';
  const histories = visibleAll(document, historySelector);
  const composers = visibleAll(document, composerSelector);
  if (histories.length !== 1 || !exactRoomScope(histories[0])) {
    return reject("One exact room-bound history was not present at commit");
  }
  if (composers.length !== 1 || !exactRoomScope(composers[0])) {
    return reject("One exact room-bound composer was not present at commit");
  }
  const historyState = histories[0].__upworkMcpMessageCommitGuard;
  const composerState = composers[0].__upworkMcpMessageCommitGuard;
  if (!historyState || historyState !== composerState || historyState.token !== args.token ||
      !(historyState.observer instanceof MutationObserver)) {
    return reject("The atomic room/history guard was replaced or unavailable");
  }
  const cleanup = () => {
    historyState.observer.disconnect();
    if (historyState.inputListener) composers[0].removeEventListener("input", historyState.inputListener, true);
    if (historyState.changeListener) composers[0].removeEventListener("change", historyState.changeListener, true);
    if (EventTarget.prototype.addEventListener === historyState.addEventListenerWrapper) {
      EventTarget.prototype.addEventListener = historyState.originalAddEventListener;
    }
    if (EventTarget.prototype.removeEventListener === historyState.removeEventListenerWrapper) {
      EventTarget.prototype.removeEventListener = historyState.originalRemoveEventListener;
    }
  };
  historyState.generation += historyState.observer.takeRecords().length;
  if (historyState.generation !== args.generation ||
      historyState.eventGeneration !== args.eventGeneration ||
      historyState.handlerGeneration !== args.handlerGeneration ||
      EventTarget.prototype.addEventListener !== historyState.addEventListenerWrapper ||
      EventTarget.prototype.removeEventListener !== historyState.removeEventListenerWrapper) {
    cleanup();
    return reject("The room history, composer, or input event state changed during final readback");
  }
  const history = histories[0];
  const virtualized = normalized(history.getAttribute("data-virtualized")).toLowerCase() === "true" ||
    normalized(history.getAttribute("data-windowed")).toLowerCase() === "true" ||
    normalized(history.getAttribute("data-test")).toLowerCase().includes("virtualized") ||
    normalized(history.getAttribute("class")).toLowerCase().includes("virtualized");
  const incompleteSelector = '[data-test="load-older-messages"], ' +
    '[data-test="load-earlier-messages"], [data-virtualized="true"], ' +
    '[data-test*="virtualized-message-list"]';
  const incompleteButtons = visibleAll(history, "button").filter((button) =>
    ["load older messages", "load earlier messages"].includes(normalized(button.textContent).toLowerCase()));
  if (virtualized || visibleAll(history, incompleteSelector).length || incompleteButtons.length) {
    cleanup();
    return reject("The owner history became partial or virtualized at commit");
  }
  const recordSelector = '[data-test="message"], .message-item, .chat-message';
  const records = visibleAll(history, recordSelector);
  const boundarySelector = '[data-test="message-history-complete"], ' +
    '[data-test="message-history-start"][data-history-complete="true"], ' +
    '[data-test="conversation-start"][data-history-complete="true"]';
  const boundaries = visibleAll(history, boundarySelector);
  const boundaryCount = boundaries.length === 1 ?
    normalized(boundaries[0].getAttribute("data-message-count")) : "";
  if (boundaries.length !== 1 ||
      normalized(boundaries[0].getAttribute("data-history-complete")).toLowerCase() !== "true" ||
      !/^[0-9]+$/.test(boundaryCount) || Number(boundaryCount) !== records.length) {
    cleanup();
    return reject("The complete-history boundary did not match at commit");
  }
  const bodySelectors = ['[data-test="content"]', '.message-text', '.content', 'p'];
  const identityAttributes = ["data-message-id", "data-message-uid", "data-test-key", "id"];
  const canonical = [];
  const seenIdentities = new Set();
  for (const record of records) {
    const bodies = [];
    for (const selector of bodySelectors) {
      const matches = visibleAll(record, selector);
      if (matches.length > 1) {
        cleanup();
        return reject("A message body became ambiguous at commit");
      }
      if (matches.length === 1) bodies.push(matches[0].textContent);
    }
    if (!bodies.length || bodies.some((body) => body !== bodies[0])) {
      cleanup();
      return reject("A message body changed or became ambiguous at commit");
    }
    let identity = null;
    for (const attribute of identityAttributes) {
      const value = normalized(record.getAttribute(attribute));
      if (value) { identity = `${attribute}:${value}`; break; }
    }
    if (!identity) {
      cleanup();
      return reject("A message identity disappeared at commit");
    }
    if (seenIdentities.has(identity)) {
      cleanup();
      return reject("A duplicate owner-system message identity appeared at commit");
    }
    seenIdentities.add(identity);
    const dataTest = normalized(record.getAttribute("data-test")).toLowerCase();
    const classes = Array.from(record.classList).map((value) => value.toLowerCase());
    const mine = Boolean(record.querySelector('.my-message, [data-test="my-message"], .sent')) ||
      dataTest === "my-message" || normalized(record.getAttribute("data-is-mine")).toLowerCase() === "true" ||
      ["outbound", "sent"].includes(normalized(record.getAttribute("data-direction")).toLowerCase()) ||
      classes.some((value) => ["my-message", "sent", "outbound"].includes(value));
    const other = Boolean(record.querySelector('.their-message, [data-test="other-message"], ' +
      '[data-test="received-message"], .received, .incoming')) ||
      ["other-message", "received-message"].includes(dataTest) ||
      normalized(record.getAttribute("data-is-mine")).toLowerCase() === "false" ||
      ["inbound", "received"].includes(normalized(record.getAttribute("data-direction")).toLowerCase()) ||
      classes.some((value) => ["their-message", "received", "incoming", "inbound"].includes(value));
    if (mine === other) {
      cleanup();
      return reject("A message owner became ambiguous at commit");
    }
    canonical.push({identity, content: bodies[0], is_mine: mine});
  }
  if (canonical.length !== args.expectedRecords.length || canonical.some((record, index) => {
    const expected = args.expectedRecords[index];
    return !expected || record.identity !== expected.identity ||
      record.content !== expected.content || record.is_mine !== expected.is_mine;
  })) {
    cleanup();
    return reject("The complete conversation snapshot changed at commit");
  }
  const inputSelector = 'textarea[data-test="message-input"], textarea[name*="message"], ' +
    '[contenteditable="true"][data-test="message-input"]';
  const inputs = visibleAll(composers[0], inputSelector);
  if (inputs.length !== 1) {
    cleanup();
    return reject("One exact composer input was not present at commit");
  }
  const inputValue = inputs[0].isContentEditable ? inputs[0].textContent : inputs[0].value;
  if (inputValue !== args.message) {
    cleanup();
    return reject("The exact approved composer copy changed at commit");
  }
  const sendSelector = '[data-test="send-button"], [data-test="send-message-button"], ' +
    'button[type="submit"], button[aria-label="Send"], button[aria-label="Send message"]';
  const candidates = visibleAll(composers[0], sendSelector).filter((candidate) => {
    if (!(candidate instanceof HTMLButtonElement)) return false;
    const dataTest = normalized(candidate.getAttribute("data-test")).toLowerCase();
    const aria = normalized(candidate.getAttribute("aria-label")).toLowerCase();
    const type = normalized(candidate.getAttribute("type")).toLowerCase();
    const text = normalized(candidate.textContent).toLowerCase();
    const semanticSend = ["send", "send message"].includes(aria) ||
      ["send", "send message"].includes(text);
    const knownSend = ["send-button", "send-message-button"].includes(dataTest) ||
      ["send", "send message"].includes(aria) || type === "submit";
    return semanticSend && knownSend && !candidate.disabled &&
      normalized(candidate.getAttribute("aria-disabled")).toLowerCase() !== "true";
  });
  if (candidates.length !== 1) {
    cleanup();
    return reject("One exact enabled room-scoped Send control was not present at commit");
  }
  const send = candidates[0];
  const actionForm = send.closest("form");
  if (send !== historyState.actionTarget || send.onclick !== historyState.actionOnClick ||
      (actionForm ? actionForm.onsubmit : null) !== historyState.formOnSubmit ||
      document.onclick !== historyState.documentOnClick ||
      document.onsubmit !== historyState.documentOnSubmit) {
    cleanup();
    return reject("The exact Send action handler changed during final readback");
  }
  if (!send.isConnected || !visible(send) || !composers[0].contains(send)) {
    cleanup();
    return reject("The exact room-scoped Send control detached before dispatch");
  }
  cleanup();
  try {
    HTMLElement.prototype.click.call(send);
  } catch (_error) {
    return {status: "unknown", dispatchStarted: true, message: "The single atomic Send dispatch raised"};
  }
  return {status: "clicked", dispatchStarted: true};
}
"""


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
    history_snapshot_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="Exact complete conversation-history snapshot approved by the owner",
    )
    history_record_count: int = Field(
        ge=0,
        description="Number of records in the complete approval-bound history",
    )
    last_message_identity_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="Identity digest of the approval-bound final message, or null for an empty room",
    )
    history_completeness_proof: str = Field(
        pattern=r"^exact_owner_complete_boundary$",
        description="Proof mode used to establish that the entire room history was rendered",
    )
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
        if self.history_record_count == 0 and self.last_message_identity_sha256 is not None:
            raise ValueError("An empty approved history cannot include a last-message identity")
        if self.history_record_count > 0 and self.last_message_identity_sha256 is None:
            raise ValueError("A non-empty approved history requires a last-message identity")
        return self


def message_payload(params: SendMessageParams) -> dict[str, Any]:
    return {
        "room_url": params.room_url,
        "room_id": params.room_id,
        "contact_name": params.contact_name,
        "message": params.message,
        "history_snapshot_sha256": params.history_snapshot_sha256,
        "history_record_count": params.history_record_count,
        "last_message_identity_sha256": params.last_message_identity_sha256,
        "history_completeness_proof": params.history_completeness_proof,
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
        history = await _complete_message_history(page, room_id)
    identity = _conversation_identity(conversation)
    validation = validate_upwork_copy(message)
    errors = list(validation["errors"])
    if identity is None:
        errors.append("The message room and recipient identity could not be read back from Upwork")
    elif identity["room_id"] != room_id:
        errors.append("Upwork returned a different message room than requested")
    if history["status"] != "complete":
        errors.append(
            "The entire conversation history could not be proved complete, so the message "
            "context cannot be bound for approval"
        )

    payload: dict[str, Any] | None = None
    prepared = None
    history_approval = _message_history_approval(history)
    if identity is not None and history_approval is not None:
        params = SendMessageParams(
            room_url=identity["room_url"],
            room_id=identity["room_id"],
            contact_name=identity["contact_name"],
            message=message,
            **history_approval,
        )
        payload = message_payload(params)
        if not errors:
            prepared = prepare_action("message", payload)

    return {
        **validation,
        "valid": not errors,
        "errors": list(dict.fromkeys(errors)),
        "current_conversation": identity,
        "history_readback": history,
        "history_approval": history_approval,
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

    bodies: list[str] = []
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
            bodies.append(content)
    if not bodies:
        return None, "A visible message record did not expose a readable body"
    first = bodies[0]
    if any(not _same_utf8_bytes(first, body) for body in bodies[1:]):
        return None, "A visible message record exposed competing body representations"
    return first, None


async def _strict_message_record(element) -> tuple[dict[str, Any] | None, str | None]:
    """Read the exact body and ownership of one currently rendered message record."""

    content, error = await _strict_message_content(element)
    if error:
        return None, error
    try:
        mine_indicator = await element.query_selector(_OWN_MESSAGE_SELECTOR)
        other_indicator = await element.query_selector(_OTHER_MESSAGE_SELECTOR)
        data_test = (await element.get_attribute("data-test") or "").strip().casefold()
        class_names = (await element.get_attribute("class") or "").split()
        data_is_mine = (await element.get_attribute("data-is-mine") or "").strip().casefold()
        data_direction = (await element.get_attribute("data-direction") or "").strip().casefold()
        identities = [
            (attribute, str(value).strip())
            for attribute in _MESSAGE_IDENTITY_ATTRIBUTES
            if (value := await element.get_attribute(attribute)) is not None
            and str(value).strip()
        ]
    except Exception:
        return None, "A visible message record's ownership or identity could not be read"
    if not identities:
        return None, "A visible message record exposed no stable owner-system identity"
    identity_attribute, identity_value = identities[0]
    explicit_mine = (
        mine_indicator is not None
        or data_test == "my-message"
        or data_is_mine == "true"
        or data_direction in {"outbound", "sent"}
        or any(name.casefold() in {"my-message", "sent", "outbound"} for name in class_names)
    )
    explicit_other = (
        other_indicator is not None
        or data_test in {"other-message", "received-message"}
        or data_is_mine == "false"
        or data_direction in {"inbound", "received"}
        or any(
            name.casefold() in {"their-message", "received", "incoming", "inbound"}
            for name in class_names
        )
    )
    if explicit_mine == explicit_other:
        return None, "A visible message record's sender ownership was ambiguous"
    return {
        "identity": f"{identity_attribute}:{identity_value}",
        "content": content,
        "is_mine": explicit_mine,
    }, None


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
    seen_identities: set[str] = set()
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
        if record["identity"] in seen_identities:
            return {
                "status": "incomplete",
                "messages": records,
                "rendered_record_count": len(elements),
                "unreadable_record_index": index,
                "message": "Visible message records exposed a duplicate owner-system identity",
            }
        seen_identities.add(record["identity"])
        records.append(record)
    return {
        "status": "complete",
        "messages": records,
        "rendered_record_count": len(elements),
        "message": "Every currently rendered message record was read",
    }


def _sha256_json(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _history_records(history: dict[str, Any]) -> list[dict[str, Any]] | None:
    records = history.get("messages")
    if not isinstance(records, list):
        return None
    if not all(
        isinstance(record, dict)
        and isinstance(record.get("identity"), str)
        and bool(record.get("identity"))
        and isinstance(record.get("content"), str)
        and type(record.get("is_mine")) is bool
        for record in records
    ):
        return None
    return records


def _message_history_approval(history: dict[str, Any]) -> dict[str, Any] | None:
    """Return privacy-minimal deterministic approval fields for one complete history."""

    if history.get("status") != "complete" or history.get("completeness_proof") != (
        "exact_owner_complete_boundary"
    ):
        return None
    records = _history_records(history)
    if records is None or history.get("rendered_record_count") != len(records):
        return None
    canonical = [
        {
            "ordinal": index,
            "identity": record["identity"],
            "content": record["content"],
            "is_mine": record["is_mine"],
        }
        for index, record in enumerate(records)
    ]
    last_identity = (
        _sha256_json({"owner_message_identity": canonical[-1]["identity"]})
        if canonical
        else None
    )
    return {
        "history_snapshot_sha256": _sha256_json(canonical),
        "history_record_count": len(canonical),
        "last_message_identity_sha256": last_identity,
        "history_completeness_proof": "exact_owner_complete_boundary",
    }


async def _history_has_complete_owner_boundary(page, rendered_count: int) -> tuple[bool, str]:
    """Require exact owner DOM proof that the rendered history is not virtualized or partial."""

    try:
        root_virtualized = (await page.get_attribute("data-virtualized") or "").casefold()
        root_windowed = (await page.get_attribute("data-windowed") or "").casefold()
        root_test = (await page.get_attribute("data-test") or "").casefold()
        root_class = (await page.get_attribute("class") or "").casefold()
    except Exception:
        return False, "The owner-system history container attributes could not be read"
    if (
        root_virtualized == "true"
        or root_windowed == "true"
        or "virtualized" in root_test
        or "virtualized" in root_class
    ):
        return False, "The owner-system history container is virtualized"

    incomplete = await _visible_elements(page, _HISTORY_INCOMPLETE_SELECTOR)
    if incomplete is None:
        return False, "History pagination and virtualization controls could not be enumerated"
    if incomplete:
        return False, "The owner-system history is paginated or virtualized"

    boundaries = await _visible_elements(page, _HISTORY_COMPLETE_BOUNDARY_SELECTOR)
    if boundaries is None:
        return False, "The complete-history boundary could not be enumerated"
    if len(boundaries) != 1:
        return False, "Exactly one owner-system complete-history boundary was not found"
    boundary = boundaries[0]
    try:
        complete = (await boundary.get_attribute("data-history-complete") or "").casefold()
        raw_count = await boundary.get_attribute("data-message-count")
        expected_count = int(str(raw_count))
    except Exception:
        return False, "The complete-history boundary attributes could not be read"
    if complete != "true" or expected_count != rendered_count:
        return False, "The owner-system history boundary does not match the rendered record count"
    return True, "Exact non-virtualized owner-system boundary and record count were read"


async def _scope_matches_room(scope, expected_room_id: str) -> bool:
    try:
        identities = {
            str(value).strip()
            for attribute in _ROOM_SCOPE_IDENTITY_ATTRIBUTES
            if (value := await scope.get_attribute(attribute)) is not None
            and str(value).strip()
        }
    except Exception:
        return False
    return identities == {expected_room_id}


async def _resolve_exact_history_scope(
    page,
    expected_room_id: str,
) -> tuple[Any | None, str | None]:
    scopes = await _visible_elements(page, _MESSAGE_HISTORY_CONTAINER_SELECTOR)
    if scopes is None:
        return None, "The owner-system message-history containers could not be enumerated"
    if len(scopes) != 1:
        return None, "Exactly one visible owner-system message-history container was not found"
    if not await _scope_matches_room(scopes[0], expected_room_id):
        return None, "The owner-system message-history container was not bound to the exact room"
    return scopes[0], None


async def _complete_message_history(page, expected_room_id: str) -> dict[str, Any]:
    """Read a stable complete history, failing closed on virtualization or missing boundaries."""

    first_scope, first_scope_error = await _resolve_exact_history_scope(page, expected_room_id)
    if first_scope is None:
        return {
            "status": "incomplete",
            "messages": [],
            "completeness_proof": None,
            "message": first_scope_error,
        }
    first = await _visible_message_history(first_scope)
    if first.get("status") != "complete":
        return first
    first_records = _history_records(first)
    if first_records is None:
        return {
            **first,
            "status": "incomplete",
            "message": "The rendered message records could not form a deterministic snapshot",
        }
    first_boundary, first_note = await _history_has_complete_owner_boundary(
        first_scope,
        len(first_records),
    )
    if not first_boundary:
        return {
            **first,
            "status": "incomplete",
            "completeness_proof": None,
            "message": first_note,
        }

    try:
        await page.wait_for_timeout(100)
    except Exception:
        await asyncio.sleep(0)
    second_scope, second_scope_error = await _resolve_exact_history_scope(page, expected_room_id)
    if second_scope is None:
        return {
            "status": "incomplete",
            "messages": [],
            "completeness_proof": None,
            "message": second_scope_error,
        }
    second = await _visible_message_history(second_scope)
    second_records = _history_records(second)
    if second.get("status") != "complete" or second_records is None:
        return {
            **second,
            "status": "incomplete",
            "completeness_proof": None,
            "message": "The complete history could not be read twice",
        }
    second_boundary, second_note = await _history_has_complete_owner_boundary(
        second_scope,
        len(second_records),
    )
    if not second_boundary:
        return {
            **second,
            "status": "incomplete",
            "completeness_proof": None,
            "message": second_note,
        }
    first_approval = {
        "status": "complete",
        "completeness_proof": "exact_owner_complete_boundary",
        "messages": first_records,
        "rendered_record_count": len(first_records),
    }
    second_approval = {
        "status": "complete",
        "completeness_proof": "exact_owner_complete_boundary",
        "messages": second_records,
        "rendered_record_count": len(second_records),
    }
    if _message_history_approval(first_approval) != _message_history_approval(second_approval):
        return {
            **second,
            "status": "incomplete",
            "completeness_proof": None,
            "message": "The complete history changed between stable owner-system readbacks",
        }
    return {
        **second,
        "status": "complete",
        "completeness_proof": "exact_owner_complete_boundary",
        "message": first_note,
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
    """Compare two complete histories through their deterministic approval snapshots."""

    left = _message_history_approval(baseline)
    right = _message_history_approval(current)
    return left is not None and left == right


def _approved_history_matches(params: SendMessageParams, history: dict[str, Any]) -> bool:
    approval = _message_history_approval(history)
    if approval is None:
        return False
    expected = {
        "history_snapshot_sha256": params.history_snapshot_sha256,
        "history_record_count": params.history_record_count,
        "last_message_identity_sha256": params.last_message_identity_sha256,
        "history_completeness_proof": params.history_completeness_proof,
    }
    return approval == expected


async def _restore_composer_value(input_element, original_value: str) -> bool:
    """Restore and exactly verify the currently rendered composer value."""

    try:
        await input_element.fill(original_value)
    except Exception:
        return False
    restored = await _read_composer_value(input_element)
    return restored is not None and _same_utf8_bytes(restored, original_value)


async def _restore_and_verify_persisted_composer(
    page,
    input_element,
    *,
    room_url: str,
    approved_identity: dict[str, str],
) -> tuple[bool, dict[str, Any]]:
    """Clear a possible autosaved draft, reload the room, and prove it stayed clear."""

    details: dict[str, Any] = {
        "local_composer_cleared": False,
        "room_reloaded": False,
        "persisted_composer_cleared": False,
    }
    if not await _restore_composer_value(input_element, ""):
        return False, details
    details["local_composer_cleared"] = True
    try:
        await page.wait_for_timeout(250)
    except Exception:
        await asyncio.sleep(0)
    try:
        reload_method = getattr(page, "reload", None)
        if callable(reload_method):
            await reload_method(wait_until="networkidle")
        else:
            await page.goto(room_url, wait_until="networkidle")
    except Exception:
        return False, details
    details["room_reloaded"] = True

    identity, identity_error = await _current_conversation_identity(page)
    details["identity_read_error"] = identity_error
    if identity != approved_identity:
        return False, details
    _, reloaded_input, composer_error = await _resolve_exact_composer(
        page,
        approved_identity["room_id"],
    )
    details["composer_read_error"] = composer_error
    if reloaded_input is None:
        return False, details
    persisted_value = await _read_composer_value(reloaded_input)
    if persisted_value is None or not _same_utf8_bytes(persisted_value, ""):
        return False, details
    details["persisted_composer_cleared"] = True
    return True, details


async def _preclick_failure(
    page,
    input_element,
    *,
    room_url: str,
    approved_identity: dict[str, str],
    status: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Restore and reload-prove a blank composer or report unknown draft state."""

    restored, restoration = await _restore_and_verify_persisted_composer(
        page,
        input_element,
        room_url=room_url,
        approved_identity=approved_identity,
    )
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
            "draft_restoration_readback": restoration,
            "composer_restored": False,
            "external_action_taken": True,
        }
    return {
        "status": status,
        "message": message,
        **(details or {}),
        "draft_restoration_readback": restoration,
        "composer_restored": True,
        "external_action_taken": False,
    }


async def _resolve_exact_composer(
    page,
    expected_room_id: str,
) -> tuple[Any | None, Any | None, str | None]:
    """Resolve one visible composer and one writable field scoped inside it."""

    composers = await _visible_elements(page, _COMPOSER_SELECTOR)
    if composers is None:
        return None, None, "The message composers could not be completely enumerated"
    if len(composers) != 1:
        return None, None, "Exactly one visible message composer was not found"
    if not await _scope_matches_room(composers[0], expected_room_id):
        return None, None, "The exact message composer was not bound to the approved room"
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
    if len(candidates) != 1:
        return None, "Exactly one visible composer-scoped Send candidate was not found"
    exact = []
    for candidate in candidates:
        identity = await _exact_send_control_identity(candidate)
        if identity is None:
            return None, "A composer-scoped Send candidate could not be completely read"
        if identity:
            exact.append(candidate)
    if len(exact) != 1:
        return None, "Exactly one exact composer-scoped Send control was not found"
    try:
        if not await exact[0].is_enabled():
            return None, "The exact composer-scoped Send control was disabled"
    except Exception:
        return None, "The exact composer-scoped Send control state could not be read"
    return exact[0], None


async def _exact_send_control_identity(candidate) -> bool | None:
    """Read whether one live control is still exactly the scoped Send action."""

    try:
        data_test = (await candidate.get_attribute("data-test") or "").casefold()
        aria_label = (await candidate.get_attribute("aria-label") or "").strip().casefold()
        control_type = (await candidate.get_attribute("type") or "").casefold()
        text = re.sub(r"\s+", " ", (await candidate.text_content() or "")).strip().casefold()
    except Exception:
        return None
    return bool(
        data_test in {"send-button", "send-message-button"}
        or aria_label in {"send", "send message"}
        or (control_type == "submit" and text in {"send", "send message"})
    )


async def _install_message_commit_guard(
    page,
    *,
    room_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Install a browser-main-thread mutation barrier before final history readback."""

    token = secrets.token_hex(24)
    try:
        result = await page.evaluate(
            _MESSAGE_COMMIT_GUARD_SCRIPT,
            {
                "operation": "install_message_commit_guard",
                "roomId": room_id,
                "token": token,
            },
        )
    except Exception:
        return None, "The atomic room/history commit guard could not be installed"
    if not isinstance(result, dict) or result.get("status") != "ready":
        message = result.get("message") if isinstance(result, dict) else None
        return None, str(message or "The atomic room/history commit guard was unavailable")
    generation = result.get("generation")
    event_generation = result.get("eventGeneration")
    handler_generation = result.get("handlerGeneration")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
        return None, "The atomic room/history commit guard returned no valid generation"
    if (
        not isinstance(event_generation, int)
        or isinstance(event_generation, bool)
        or event_generation < 0
    ):
        return None, "The atomic room/history commit guard returned no valid event generation"
    if (
        not isinstance(handler_generation, int)
        or isinstance(handler_generation, bool)
        or handler_generation < 0
    ):
        return None, "The atomic room/history commit guard returned no valid handler generation"
    return {
        "token": token,
        "generation": generation,
        "event_generation": event_generation,
        "handler_generation": handler_generation,
    }, None


async def _atomic_message_commit(
    page,
    *,
    params: SendMessageParams,
    history: dict[str, Any],
    guard: dict[str, Any],
) -> tuple[str, str | None]:
    """Validate room/history/composer/Send and dispatch once in one browser task."""

    records = _history_records(history)
    if records is None:
        return "rejected", "The final complete message snapshot was unavailable"
    try:
        result = await page.evaluate(
            _ATOMIC_MESSAGE_COMMIT_SCRIPT,
            {
                "operation": "atomic_message_commit",
                "roomUrl": params.room_url,
                "roomId": params.room_id,
                "contactName": params.contact_name,
                "message": params.message,
                "expectedRecords": records,
                "token": guard["token"],
                "generation": guard["generation"],
                "eventGeneration": guard["event_generation"],
                "handlerGeneration": guard["handler_generation"],
            },
        )
    except Exception:
        # The browser task may have dispatched Send before its execution context
        # or result channel failed. Treat every evaluation exception as an
        # irreversible unknown and rely only on owner-system readback.
        return "unknown", "The atomic room/history/Send commit outcome is unknown"
    if not isinstance(result, dict):
        return "unknown", "The atomic room/history/Send commit returned no proof"
    status = result.get("status")
    message = result.get("message")
    dispatch_started = result.get("dispatchStarted")
    if status == "rejected" and dispatch_started is not False:
        return "unknown", "The atomic room/history/Send rejection lacked pre-dispatch proof"
    if status in {"clicked", "unknown"} and dispatch_started is not True:
        return "unknown", "The atomic room/history/Send result lacked dispatch proof"
    if status not in {"clicked", "rejected", "unknown"}:
        return "unknown", "The atomic room/history/Send commit returned invalid proof"
    return status, str(message) if message else None


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

    history_before = await _complete_message_history(page, params.room_id)
    if history_before["status"] != "complete":
        return {
            "status": "history_unreadable",
            "message": (
                "The entire owner-system conversation history could not be proved complete; "
                "nothing was sent."
            ),
            "visible_history_readback": history_before,
            "external_action_taken": False,
        }
    if not _approved_history_matches(params, history_before):
        return {
            "status": "message_history_changed_since_approval",
            "message": (
                "The complete conversation history differs from the owner-approved snapshot; "
                "nothing was sent. Review the current room and prepare the message again."
            ),
            "approved_history": {
                "history_snapshot_sha256": params.history_snapshot_sha256,
                "history_record_count": params.history_record_count,
                "last_message_identity_sha256": params.last_message_identity_sha256,
                "history_completeness_proof": params.history_completeness_proof,
            },
            "live_history": _message_history_approval(history_before),
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

    composer, input_el, composer_error = await _resolve_exact_composer(page, params.room_id)
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
            page,
            input_el,
            room_url=url,
            approved_identity=approved_identity,
            status="composer_unreadable",
            message="The exact approved copy could not be filled into the bound composer",
        )
    composer_readback = await _read_composer_value(input_el)
    if composer_readback is None or not _same_utf8_bytes(composer_readback, params.message):
        return await _preclick_failure(
            page,
            input_el,
            room_url=url,
            approved_identity=approved_identity,
            status="composer_readback_mismatch",
            message="The bound composer did not read back the approved copy byte-for-byte",
        )

    rebound_identity, rebound_error = await _current_conversation_identity(page)
    if rebound_identity != approved_identity:
        return await _preclick_failure(
            page,
            input_el,
            room_url=url,
            approved_identity=approved_identity,
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

    # Enumerate the current action surface before the commit barrier, but never retain
    # this handle for the click. The irreversible control is re-resolved after every
    # final target/history/composer check below.
    probed_send, probed_send_error = await _resolve_exact_scoped_send(composer)
    if probed_send_error or probed_send is None:
        return await _preclick_failure(
            page,
            input_el,
            room_url=url,
            approved_identity=approved_identity,
            status="send_control_unavailable",
            message=probed_send_error or "The exact composer-scoped Send control was unavailable",
        )

    commit_guard, guard_error = await _install_message_commit_guard(
        page,
        room_id=params.room_id,
    )
    if commit_guard is None:
        return await _preclick_failure(
            page,
            input_el,
            room_url=url,
            approved_identity=approved_identity,
            status="atomic_commit_unavailable",
            message=guard_error or "The atomic message commit guard was unavailable",
        )

    # Rebind the exact composer and room first. The complete history snapshot is
    # deliberately the last page-wide commit check before Send resolution.
    final_composer, final_input_el, final_composer_error = await _resolve_exact_composer(
        page,
        params.room_id,
    )
    if final_composer_error or final_composer is None or final_input_el is None:
        return await _preclick_failure(
            page,
            input_el,
            room_url=url,
            approved_identity=approved_identity,
            status="composer_unavailable",
            message=final_composer_error or "The exact composer changed before Send",
        )
    final_composer_readback = await _read_composer_value(final_input_el)
    if (
        final_composer_readback is None
        or not _same_utf8_bytes(final_composer_readback, params.message)
    ):
        return await _preclick_failure(
            page,
            final_input_el,
            room_url=url,
            approved_identity=approved_identity,
            status="composer_readback_mismatch",
            message="The approved copy changed before the exact Send control could be clicked",
        )

    final_identity, final_identity_error = await _current_conversation_identity(page)
    if final_identity != approved_identity:
        return await _preclick_failure(
            page,
            final_input_el,
            room_url=url,
            approved_identity=approved_identity,
            status="live_identity_mismatch",
            message=(
                "The live conversation or recipient changed immediately before Send; "
                "nothing was sent."
            ),
            details={
                "identity_read_error": final_identity_error,
                "approved_conversation_identity": approved_identity,
                "live_conversation_identity": final_identity,
            },
        )

    final_history = await _complete_message_history(page, params.room_id)
    if final_history["status"] != "complete":
        return await _preclick_failure(
            page,
            final_input_el,
            room_url=url,
            approved_identity=approved_identity,
            status="history_unreadable",
            message=(
                "The complete owner-system history could not be re-read immediately before "
                "Send; nothing was sent."
            ),
            details={"visible_history_readback": final_history},
        )
    if not _same_complete_visible_history(history_before, final_history):
        return await _preclick_failure(
            page,
            final_input_el,
            room_url=url,
            approved_identity=approved_identity,
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

    # One browser-main-thread operation now re-resolves the exact room-bound Send
    # control, proves that the guarded complete history/composer did not mutate,
    # validates target and copy, and dispatches the action exactly once. No
    # browser event-loop turn exists between its final validation and dispatch.
    commit_status, commit_error = await _atomic_message_commit(
        page,
        params=params,
        history=final_history,
        guard=commit_guard,
    )
    if commit_status == "rejected":
        return await _preclick_failure(
            page,
            final_input_el,
            room_url=url,
            approved_identity=approved_identity,
            status="atomic_commit_rejected",
            message=commit_error or "The atomic room/history/Send commit was rejected",
        )
    atomic_click_uncertain = commit_status == "unknown"

    # Confirm a *new* matching owner message. A cleared input alone is not proof,
    # and the complete pre-send history prevents an older identical message from
    # being mistaken for the write performed by this call.
    last_history = history_before
    for _ in range(20):
        last_history = await _complete_message_history(page, params.room_id)
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
        input_after_send = await _read_composer_value(final_input_el)
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
                    "atomic_click_acknowledged": not atomic_click_uncertain,
                },
                "external_action_taken": True,
            }
        await asyncio.sleep(0.5)

    input_value = await _read_composer_value(final_input_el)
    return {
        "status": "unknown",
        "message": (
            "The atomic Send dispatch outcome was uncertain and Upwork did not show a new "
            "exact-copy message; do not retry automatically."
            if atomic_click_uncertain
            else "Upwork did not show a new exact-copy message; do not retry automatically."
        ),
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
