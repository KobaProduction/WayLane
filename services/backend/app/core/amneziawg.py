"""Validation of AmneziaWG 3.1 device parameters in a WireGuard core.

The crypto identity stays WireGuard; these parameters configure transport
obfuscation and must match the client export for server-wide options.
"""
import base64
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_CPS = re.compile(r"^(?:<t>|<b 0x(?:[a-fA-F0-9]{2})+>|<(?:r|rd|rc) (?:[1-9][0-9]{0,2}|1000)>)+$")


def _parse_range(name: str, value: str, maximum: int) -> tuple[int, int]:
    parts = value.split("-")
    if len(parts) not in (1, 2) or any(not part.isdecimal() for part in parts):
        raise ValueError(f"{name} must be an integer or min-max")
    start = int(parts[0])
    end = int(parts[-1])
    if not 0 <= start <= end <= maximum:
        raise ValueError(f"{name} is outside its allowed range")
    return start, end


class AmneziaWG31(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jc: int = Field(default=0, ge=0, le=65535)
    jmin: int = Field(default=0, ge=0, le=65535)
    jmax: int = Field(default=0, ge=0, le=65535)
    s1: int = Field(default=0, ge=0, le=65535)
    s2: int = Field(default=0, ge=0, le=65535)
    s3: int = Field(default=0, ge=0, le=65535)
    s4: int = Field(default=0, ge=0, le=65535)
    h1: str = ""
    h2: str = ""
    h3: str = ""
    h4: str = ""
    i1: str = ""
    i2: str = ""
    i3: str = ""
    i4: str = ""
    i5: str = ""
    header_protection_key: str = ""
    content_padding_addition: str = ""
    rekey_after_time: str = ""
    rekey_timeout: str = ""
    reject_after_time: str = ""
    keepalive_timeout: str = ""
    max_handshake_attempts: str = ""
    random_trailers: bool = False
    disable_cookies: bool = False

    @field_validator(
        "h1", "h2", "h3", "h4", "content_padding_addition",
        "rekey_after_time", "rekey_timeout", "reject_after_time",
        "keepalive_timeout", "max_handshake_attempts",
    )
    @classmethod
    def check_ranges(cls, value: str, info):
        if value:
            maximum = 2**32 - 1 if info.field_name.startswith("h") else 65535
            _parse_range(info.field_name, value, maximum)
        return value

    @field_validator("i1", "i2", "i3", "i4", "i5")
    @classmethod
    def check_cps(cls, value: str):
        if value and (len(value) > 4096 or not _CPS.fullmatch(value)):
            raise ValueError("invalid AmneziaWG CPS packet tags")
        return value

    @field_validator("header_protection_key")
    @classmethod
    def check_header_key(cls, value: str):
        if value:
            try:
                decoded = base64.b64decode(value, validate=True)
            except ValueError as exc:
                raise ValueError("invalid HeaderProtectionKey encoding") from exc
            if len(decoded) != 32:
                raise ValueError("HeaderProtectionKey must contain 32 bytes")
        return value

    @model_validator(mode="after")
    def check_coherence(self):
        if self.jc > 0 and self.jmin > self.jmax:
            raise ValueError("jmin exceeds jmax")
        if self.header_protection_key and any(
            getattr(self, field) < 12 for field in ("s1", "s2", "s3", "s4")
        ):
            raise ValueError("s1-s4 must all be >=12 when HeaderProtectionKey is set")
        headers: list[tuple[str, tuple[int, int]]] = []
        for index in range(1, 5):
            name = f"h{index}"
            raw = getattr(self, name)
            if not raw:
                continue
            bounds = _parse_range(name, raw, 2**32 - 1)
            if bounds == (index, index):
                continue
            for previous, (low, high) in headers:
                if bounds[0] <= high and low <= bounds[1]:
                    raise ValueError(f"{name} overlaps {previous}")
            headers.append((name, bounds))
        return self
