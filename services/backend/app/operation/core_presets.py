"""Validate managed Xray presets and hydrate only the node-facing payload."""

import json
from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.core_presets import core_preset_bindings, core_presets
from app.operation.tls_presets import TlsPresetError, decrypt_private_key, encrypt_private_key
from app.utils.crypto import get_x25519_public_key, validate_mldsa65_seed, validate_mldsa65_verify


class CorePresetError(ValueError):
    """A managed core preset, binding or materialization is invalid."""


KINDS = ("reality", "vless", "routing")
ROUTING_TAG = "__routing__"


def build_preset(kind: str, payload: dict) -> tuple[dict, str | None]:
    """Return public metadata and encrypted secret (never raw private material)."""
    if kind == "reality":
        private_key = str(payload.get("private_key") or "").strip()
        if not private_key:
            raise CorePresetError("REALITY private_key is required")
        try:
            public_key = get_x25519_public_key(private_key)
        except Exception as exc:
            raise CorePresetError("Invalid REALITY private key") from exc
        if not public_key:
            raise CorePresetError("Cannot derive REALITY public key")
        if provided := payload.get("public_key"):
            if provided != public_key:
                raise CorePresetError("REALITY public key does not match private key")
        short_ids = payload.get("short_ids", [])
        if not isinstance(short_ids, list) or not short_ids or not all(
            isinstance(sid, str) and len(sid) <= 16 and len(sid) % 2 == 0
            and all(c in "0123456789abcdefABCDEF" for c in sid)
            for sid in short_ids
        ):
            raise CorePresetError("REALITY requires valid hexadecimal short IDs")
        seed = payload.get("mldsa65_seed") or ""
        verify = payload.get("mldsa65_verify") or ""
        if verify and not seed:
            raise CorePresetError("ML-DSA65 verify requires matching seed")
        if seed:
            validate_mldsa65_seed(seed)
        if verify:
            validate_mldsa65_verify(verify)
        secret = {"privateKey": private_key}
        if seed:
            secret["mldsa65Seed"] = seed
        public = {"publicKey": public_key, "shortIds": short_ids}
        if verify:
            public["mldsa65Verify"] = verify
    elif kind == "vless":
        decrypt = str(payload.get("decryption") or "").strip()
        encrypt = str(payload.get("encryption") or "").strip()
        if not decrypt or decrypt == "none" or not encrypt or encrypt == "none":
            raise CorePresetError("VLESS preset requires matching server decryption and client encryption")
        if not (decrypt.startswith("mlkem768x25519plus.") and encrypt.startswith("mlkem768x25519plus.")):
            raise CorePresetError("Unsupported VLESS Encryption handshake")
        if decrypt.split(".")[1] != encrypt.split(".")[1]:
            raise CorePresetError("VLESS encryption and decryption traffic modes must match")
        if len(decrypt) > 8192 or len(encrypt) > 8192:
            raise CorePresetError("VLESS encryption values exceed the limit")
        secret = {"decryption": decrypt}
        public = {"encryption": encrypt}
    elif kind == "routing":
        routing = payload.get("routing")
        if not isinstance(routing, dict) or not isinstance(routing.get("rules"), list):
            raise CorePresetError("Routing preset requires a JSON object with routing.rules array")
        # Only routing is managed: existing outbound definitions stay in core.
        if len(json.dumps(routing).encode()) > 100000:
            raise CorePresetError("Routing preset exceeds the size limit")
        if any(key in routing for key in ("waylanePresetId",)):
            raise CorePresetError("Routing preset cannot contain another preset reference")
        public = {"routing": routing}
        secret = None
    else:
        raise CorePresetError("Unsupported preset kind")
    try:
        encrypted = encrypt_private_key(json.dumps(secret)) if secret is not None else None
    except TlsPresetError as exc:
        raise CorePresetError(str(exc)) from exc
    return public, encrypted


def _find_inbound(config: dict, tag: str) -> dict:
    matches = [
        inbound for inbound in config.get("inbounds", [])
        if isinstance(inbound, dict) and inbound.get("tag") == tag
    ]
    if len(matches) != 1:
        raise CorePresetError(f"Core requires exactly one inbound matching tag {tag!r}")
    return matches[0]


def project_public_config(config: dict, kind: str, inbound_tag: str, preset_id: str, data: dict) -> dict:
    """Replace inline secret fields with a public projection and ID reference."""
    config = deepcopy(config)
    if kind == "routing":
        if inbound_tag != ROUTING_TAG:
            raise CorePresetError("Routing bindings must target the whole core")
        config["routing"] = {**deepcopy(data["routing"]), "waylanePresetId": preset_id}
        return config
    inbound = _find_inbound(config, inbound_tag)
    if kind == "reality":
        stream = inbound.get("streamSettings") or {}
        if stream.get("security") != "reality":
            raise CorePresetError("REALITY preset requires a REALITY inbound")
        settings = stream.setdefault("realitySettings", {})
        settings.pop("privateKey", None)
        settings.pop("mldsa65Seed", None)
        settings.pop("mldsa65Verify", None)
        settings.update(deepcopy(data))
        settings["waylanePresetId"] = preset_id
    elif kind == "vless":
        if inbound.get("protocol") != "vless":
            raise CorePresetError("VLESS Encryption preset requires a VLESS inbound")
        settings = inbound.setdefault("settings", {})
        settings["decryption"] = "none"
        settings["encryption"] = data["encryption"]
        settings["waylanePresetId"] = preset_id
    else:
        raise CorePresetError("Unknown binding kind")
    return config


async def project_bound_core_config(db: AsyncSession, core_id: int, config: dict) -> dict:
    """Prevent core edits reintroducing inline secrets or breaking bound refs."""
    rows = (await db.execute(
        select(core_preset_bindings.c.kind, core_preset_bindings.c.inbound_tag,
               core_presets.c.id, core_presets.c.public_data)
        .select_from(core_preset_bindings.join(core_presets, core_preset_bindings.c.preset_id == core_presets.c.id))
        .where(core_preset_bindings.c.core_id == core_id)
    )).all()
    for kind, tag, preset_id, public in rows:
        config = project_public_config(config, kind, tag, preset_id, public)
    return config


async def hydrate_core_preset_config(db: AsyncSession, raw_config: str, core_id: int) -> str:
    """Materialize server secrets only inside a node Start RPC payload."""
    rows = (await db.execute(
        select(
            core_preset_bindings.c.inbound_tag,
            core_preset_bindings.c.kind,
            core_presets.c.id,
            core_presets.c.public_data,
            core_presets.c.encrypted_secret,
        )
        .select_from(core_preset_bindings.join(core_presets, core_preset_bindings.c.preset_id == core_presets.c.id))
        .where(core_preset_bindings.c.core_id == core_id)
    )).all()
    if not rows:
        return raw_config
    config = json.loads(raw_config)
    for tag, kind, preset_id, public, encrypted in rows:
        if kind == "routing":
            if config.get("routing", {}).get("waylanePresetId") != preset_id:
                raise CorePresetError("Routing preset reference mismatch")
            config["routing"] = deepcopy(public["routing"])
        elif kind == "reality":
            inbound = _find_inbound(config, tag)
            settings = (inbound.get("streamSettings") or {}).get("realitySettings") or {}
            if settings.get("waylanePresetId") != preset_id:
                raise CorePresetError("REALITY preset reference mismatch")
            secret = json.loads(decrypt_private_key(encrypted))
            settings.pop("waylanePresetId", None)
            settings.pop("publicKey", None)
            settings.update(secret)
        elif kind == "vless":
            inbound = _find_inbound(config, tag)
            settings = inbound.get("settings") or {}
            if settings.get("waylanePresetId") != preset_id:
                raise CorePresetError("VLESS preset reference mismatch")
            secret = json.loads(decrypt_private_key(encrypted))
            settings.pop("waylanePresetId", None)
            settings["decryption"] = secret["decryption"]
        else:
            raise CorePresetError("Unsupported core preset binding")
    return json.dumps(config)
