"""Validate OCI repository/image shapes; trust is determined for each Node in DB."""
from __future__ import annotations

import re

GHCR_NODE_REPOSITORY = "ghcr.io/kobaproduction/waylane-node"
REGISTRY_HOST = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[1-9][0-9]{1,4})?$")
IMAGE_COMPONENT = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
SHA256_SUFFIX = re.compile(r"^sha256:[0-9a-f]{64}$")


def valid_repository(value: str) -> bool:
    if not isinstance(value, str) or not value or len(value) > 55 or "@" in value or "://" in value:
        return False
    parts = value.split("/")
    if len(parts) < 2 or not REGISTRY_HOST.fullmatch(parts[0]):
        return False
    if "." not in parts[0] and ":" not in parts[0]:
        return False
    if ":" in parts[0] and int(parts[0].rsplit(":", 1)[1]) > 65535:
        return False
    return all(IMAGE_COMPONENT.fullmatch(part) for part in parts[1:])


def image_repository(value: str) -> str | None:
    if not isinstance(value, str) or len(value) > 128:
        return None
    repository, sep, suffix = value.partition("@")
    if not sep or not valid_repository(repository) or not SHA256_SUFFIX.fullmatch(suffix):
        return None
    return repository


def valid_pinned_image(value: str) -> bool:
    return image_repository(value) is not None
