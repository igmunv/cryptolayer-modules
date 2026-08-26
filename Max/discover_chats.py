#!/usr/bin/env python3
"""discover_chats.py — early live-token discovery checkpoint helper.

Connects to MAX via the vkmax user-API, logs in by token, and prints a table
of available dialogs (chat_id — title/type — last message) strictly per the
«get-chats» section of docs/vkmax-contract.md (§10):

  1. Primary source: chats come from the LOGIN RESPONSE itself —
     resp["payload"]["chats"] (op=19 snapshot, chatsCount=40).
  2. Fallback/clarification for known ids: resolve_channel_id (op=48 CHAT_GET)
     from vkmax.functions.channels.

Tokens and device ids are MASKED in all output (only last 4 chars shown).
This script is a development helper — NOT part of the Max/ module folder.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

# --- masking ---------------------------------------------------------------


def mask(secret: str | None) -> str:
    """Return masked form of a secret: only its last 4 characters visible."""
    if not secret:
        return "<empty>"
    tail = secret[-4:]
    return f"***{tail}"


# --- chat extraction (contract §10) ----------------------------------------


def extract_chats(login_response: dict) -> list[dict]:
    """Extract the chats list from a login response per contract §10."""
    payload = login_response.get("payload") or {}
    return payload.get("chats", []) or []


def describe_chat(chat: dict) -> tuple[str, str, str]:
    """Return (chat_id, title_or_type, last_message_preview) from a chat dict.

    Internal field names are to be confirmed at the live checkpoint; we probe
    several plausible keys defensively instead of assuming one.
    """
    chat_id = chat.get("chatId") or chat.get("id")
    ctype = chat.get("type") or "?"
    title = chat.get("title") or chat.get("name") or ""
    label = f"{title} ({ctype})" if title else f"({ctype})"

    msg = (
        chat.get("lastMessage")
        or chat.get("message")
        or (chat.get("messagePayload") or {}).get("text")
        or ""
    )
    if isinstance(msg, dict):
        msg = msg.get("text") or ""
    preview = " ".join(str(msg).split())[:60]
    return str(chat_id), label, preview


# --- shutdown wrapper (contract §6: disconnect is NOT idempotent) ----------


async def safe_disconnect(client) -> None:
    """Idempotent-ish disconnect: swallow any exception per contract §6."""
    try:
        await client.disconnect()
    except Exception as exc:  # noqa: BLE001 - contract says generic exceptions here
        print(f"[warn] disconnect raised (ignored): {type(exc).__name__}: {exc}")


# --- main flow --------------------------------------------------------------


def _extract_token(raw: str) -> str:
    """Allow pasting the whole __oneme_auth JSON: extract .token if present."""
    text = raw.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "token" in data:
                return str(data["token"])
        except Exception:
            pass
    # strip surrounding quotes if user copied JSON string value with quotes
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1]
    return text


async def run(token: str, device_id: str, raw: bool) -> int:
    token = _extract_token(token)
    from vkmax.client import MaxClient  # lazy import; offline --help must work

    client = MaxClient()
    try:
        await client.connect()
    except Exception as exc:
        print(f"[error] connect failed ({mask(token)}): "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        await safe_disconnect(client)
        return 1

    try:
        resp = await asyncio.wait_for(
            client.login_by_token(token, device_id), timeout=30
        )
    except Exception as exc:
        # Contract §8: invalid token raises generic Exception with server text.
        print(
            f"[error] login failed for token {mask(token)} / "
            f"device {mask(device_id)}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        print("[hint] проверьте Token (__oneme_auth) и Device ID "
              "(__oneme_device_id) из LocalStorage web.max.ru", file=sys.stderr)
        await safe_disconnect(client)
        return 1

    my_id = "?"
    try:
        my_id = resp["payload"]["profile"]["contact"]["id"]
    except (KeyError, TypeError):
        try:
            my_id = resp["payload"]["profile"]["id"]
        except (KeyError, TypeError):
            print("[warn] profile.id not found in login response "
                  "(live-checkpoint observation)", file=sys.stderr)

    chats = extract_chats(resp)
    if raw:
        # Raw dump still contains no credentials — token/device never in resp.
        print(json.dumps(chats, ensure_ascii=False, indent=2))
    else:
        if my_id != "?":
            print(f"my id: {my_id}   (token {mask(token)}, device {mask(device_id)})")
        print(f"{len(chats)} dialogs:")
        print(f"{'chat_id':<22} {'название/тип':<40} последнее сообщение")
        print("-" * 100)
        for chat in chats:
            cid, label, preview = describe_chat(chat)
            print(f"{cid:<22} {label:<40} {preview}")

    await safe_disconnect(client)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="discover_chats.py",
        description="List your MAX dialogs (early live-token checkpoint helper).",
    )
    parser.add_argument("--token", required=True,
                        help="__oneme_auth из LocalStorage web.max.ru")
    parser.add_argument("--device-id", required=True,
                        help="__oneme_device_id из LocalStorage web.max.ru")
    parser.add_argument("--raw", action="store_true",
                        help="dump raw chat dicts (credentials are never included)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(run(args.token, args.device_id, args.raw))


if __name__ == "__main__":
    sys.exit(main())
