"""Explicit operator-approved Node OCI repositories, never arbitrary pull targets.

GHCR remains trusted by default. An optional *single* additional repository
may be configured on both Backend and stable Agent. Database image columns are
String(128), so the repository name is conservatively bounded to 55 bytes (128 minus the
72-character ``@sha256:<64 lowercase hex>`` suffix).
"""
from __future__ import annotations

import os
import re

GHCR_NODE_REPOSITORY = "ghcr.io/kobaproduction/waylane-node"
CUSTOM_REPOSITORY_ENV = "WAYLANE_NODE_EXTRA_IMAGE_REPOSITORY"
SHA256_SUFFIX = re.compile(r"^sha256:[0-9a-f]{64}$")
REGISTRY_HOST = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[1-9][0-9]{1,4})?$")
IMAGE_COMPONENT = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


def valid_repository(value: str) -> bool:
    if not value or len(value) > 55 or "@" in value or "://" in value:
        return False
    parts = value.split("/")
    if len(parts) < 2 or not REGISTRY_HOST.fullmatch(parts[0]):
        return False
    # An explicit registry host, not an unqualified Docker Hub namespace.
    if "." not in parts[0] and ":" not in parts[0]:
        return False
    return all(IMAGE_COMPONENT.fullmatch(part) for part in parts[1:])


def trusted_node_repositories() -> list[str]:
    custom = os.getenv(CUSTOM_REPOSITORY_ENV, "").strip()
    if custom and not valid_repository(custom):
        raise ValueError(f"Invalid {CUSTOM_REPOSITORY_ENV}: expected trusted OCI repository (max 55 characters)")
    return [GHCR_NODE_REPOSITORY] + ([custom] if custom and custom != GHCR_NODE_REPOSITORY else [])


def approved_node_image(value: str) -> bool:
    repository, separator, suffix = value.partition("@")
    return bool(separator and SHA256_SUFFIX.fullmatch(suffix) and repository in trusted_node_repositories())
