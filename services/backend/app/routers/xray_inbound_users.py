"""Safe, protocol-aware account updates for a single Xray inbound.

This is an explicitly bounded subset of the legacy PasarGuard SyncUsers
contract. It does not implement legacy user billing or traffic counters.
"""
import copy
import json
from uuid import UUID


def replace_inbound_users(config: dict, tag: str, users: list[dict]) -> dict:
    if not tag or len(tag) > 128 or len(users) > 1024:
        raise ValueError("Invalid inbound or number of users")
    updated = copy.deepcopy(config)
    inbounds = updated.get("inbounds")
    if not isinstance(inbounds, list):
        raise ValueError("Xray inbounds must be a list")
    matches = [entry for entry in inbounds if isinstance(entry, dict) and entry.get("tag") == tag]
    if len(matches) != 1:
        raise ValueError("Exactly one matching inbound tag required")
    inbound = matches[0]
    settings = inbound.get("settings")
    if not isinstance(settings, dict):
        raise ValueError("Xray inbound settings must be an object")
    protocol = inbound.get("protocol")
    field = "accounts" if protocol == "socks" else "clients"
    if protocol not in {"socks", "vless", "vmess", "trojan"}:
        raise ValueError("Unsupported inbound user protocol; no change scheduled")
    identifiers = set()
    for account in users:
        if not isinstance(account, dict):
            raise ValueError("Each user must be an object")
        if protocol == "socks":
            username = account.get("user")
            if not isinstance(username, str) or not username or len(username) > 128:
                raise ValueError("SOCKS account needs a valid user")
            secret = account.get("pass")
            if not isinstance(secret, str) or not secret or len(secret) > 256:
                raise ValueError("SOCKS account needs a password")
            key = username
        elif protocol in {"vless", "vmess"}:
            raw_id = account.get("id")
            try:
                key = str(UUID(raw_id))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("Xray client needs valid UUID id") from exc
            if protocol == "vless" and not isinstance(account.get("email"), str):
                raise ValueError("VLESS client needs email identity")
        else:
            secret = account.get("password")
            if not isinstance(secret, str) or not secret or len(secret) > 256:
                raise ValueError("Trojan client needs password")
            key = account.get("email") or secret
        if key in identifiers:
            raise ValueError("Duplicate inbound user identity")
        identifiers.add(key)
    settings[field] = copy.deepcopy(users)
    if len(json.dumps(updated, ensure_ascii=False)) > 512_000:
        raise ValueError("Updated Xray configuration exceeds 500 KiB")
    return updated
