"""Embedded legacy Subscription Firewall response format and editable scenarios.

No reverse-proxy or second user database: WayLane remains the source of truth.
Original legacy fake Shadowsocks message links are only rendered for text
subscription requests; never for HTML/API requests or as actual VPN endpoints.
"""
import base64
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.subscription_firewall import subscription_firewall_config

MESSAGE_LINK_PREFIX = (
    "ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTp2NlB3M2FUbEctM0VJX05hV3M0akZVc0FZWFY3UzM2dQ==@-:1#"
)
SCENARIOS = {
    "invalid_link": {"title": "Подписка не найдена", "lines": ["❗ССЫЛКА НЕДЕЙСТВИТЕЛЬНА❗"]},
    "device_limit": {"title": "Превышен лимит устройств", "lines": ["❗ПРЕВЫШЕН ЛИМИТ УСТРОЙСТВ❗", "Откройте кабинет и удалите старое устройство"]},
    "hwid_required": {"title": "Не определено устройство", "lines": ["❗УКАЖИТЕ УСТРОЙСТВО❗"]},
    "unsupported_client": {"title": "Клиент не поддерживается", "lines": ["❗НЕПОДДЕРЖИВАЕМОЕ ПРИЛОЖЕНИЕ❗"]},
    "rate_limited": {"title": "Слишком частое обновление", "lines": ["❗ПОДОЖДИТЕ ПЕРЕД ОБНОВЛЕНИЕМ❗"]},
    "access_denied": {"title": "Доступ отклонён", "lines": ["❗ДОСТУП К ПОДПИСКЕ ОТКЛОНЁН❗"]},
    "bot_unavailable": {"title": "Проверка устройства недоступна", "lines": ["❗ПОПРОБУЙТЕ ПОЗЖЕ❗"]},
}
ERROR_STATUSES = {
    404: "invalid_link",
    406: "unsupported_client",
    429: "rate_limited",
}


def scenario_for_error(exc: HTTPException) -> str | None:
    if exc.status_code == 403:
        detail = str(exc.detail).lower()
        if "device limit" in detail:
            return "device_limit"
        if "hwid header" in detail:
            return "hwid_required"
        return "access_denied"
    return ERROR_STATUSES.get(exc.status_code)


def render_message(title: str, lines: list[str], status_code: int = 200) -> Response:
    """Mirror legacy Base64/link + announce while avoiding raw HTML or data URLs."""
    encoded_links = "\n".join(MESSAGE_LINK_PREFIX + quote(line, safe="") for line in lines)
    encoded_title = base64.b64encode(title.encode("utf-8")).decode("ascii").rstrip("=")
    return Response(
        base64.b64encode(encoded_links.encode("utf-8")),
        status_code=status_code,
        media_type="text/plain",
        headers={"announce": f"base64:{encoded_title}", "Cache-Control": "no-store"},
    )


async def load_config(db: AsyncSession) -> dict:
    row = (await db.execute(
        select(subscription_firewall_config).where(subscription_firewall_config.c.id == 1)
    )).mappings().one_or_none()
    result = {"enabled": False, "webhook_enabled": False, "webhook_url": None,
              "device_check_url": None, "scenarios": SCENARIOS}
    if row:
        result.update(dict(row))
        result["scenarios"] = {**SCENARIOS, **(row["scenarios"] or {})}
    return result


async def response_for_failure(db: AsyncSession, exc: HTTPException, *, client_accept: str,
                               hwid: str | None, user_agent: str = "") -> Response | None:
    # Legacy Base64 + Shadowsocks notice lines are meaningful only for clients
    # which expect link subscriptions, not JSON, YAML or browser consumers.
    agent = user_agent.lower()
    if ("text/html" in client_accept or not hwid or
            not any(client in agent for client in ("happ", "v2raytun", "clienthub", "client hub"))):
        return None
    scenario = scenario_for_error(exc)
    if scenario is None:
        return None
    settings = await load_config(db)
    if not settings["enabled"]:
        return None
    current = settings["scenarios"].get(scenario, SCENARIOS[scenario])
    return render_message(current["title"], current["lines"])
