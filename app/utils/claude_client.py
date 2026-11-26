"""Claude API access with a hard monthly spending cap.

Every response's usage is priced and stored in `llm_usage`; `ensure_budget` refuses a new call once this
calendar month (UTC) has reached `CLAUDE_MONTHLY_BUDGET_USD`. In-flight calls can overshoot by a few cents.
Also set a spend limit on the key in the Claude Console as a second line of defence.
"""

import logging
from datetime import UTC, datetime
from functools import lru_cache

from anthropic import AsyncAnthropic
from anthropic.types import Usage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import LlmUsage

logger = logging.getLogger(__name__)

# USD per million tokens: (input, output) for prompts up to LONG_PROMPT_TOKENS, then (input, output) above it.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50, 0.50, 2.50),
}
LONG_PROMPT_TOKENS = 100_000
CACHE_WRITE_FACTOR = 1.25  # 5-minute cache writes cost 1.25x input; reads 0.1x
CACHE_READ_FACTOR = 0.1


class ClaudeError(RuntimeError):
    pass


class BudgetExceeded(ClaudeError):
    pass


@lru_cache
def get_claude_client() -> AsyncAnthropic:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise ClaudeError("ANTHROPIC_API_KEY is not set")
    return AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=60, max_retries=2)


def cost_usd(model: str, usage: Usage) -> float:
    if model not in PRICES:
        raise ClaudeError(f"No price for {model}: add it to PRICES so the budget can be enforced")
    cache_write = usage.cache_creation_input_tokens or 0
    cache_read = usage.cache_read_input_tokens or 0
    prompt = usage.input_tokens + cache_write + cache_read
    short_in, short_out, long_in, long_out = PRICES[model]
    price_in, price_out = (long_in, long_out) if prompt > LONG_PROMPT_TOKENS else (short_in, short_out)
    tokens = (
        usage.input_tokens * price_in
        + cache_write * price_in * CACHE_WRITE_FACTOR
        + cache_read * price_in * CACHE_READ_FACTOR
        + usage.output_tokens * price_out
    ) / 1_000_000
    return tokens + web_searches(usage) * get_settings().claude_web_search_usd


def web_searches(usage: Usage) -> int:
    return (usage.server_tool_use.web_search_requests or 0) if usage.server_tool_use else 0


def _month_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def spent_this_month(session: AsyncSession, now: datetime | None = None) -> float:
    since = _month_start(now or datetime.now(UTC))
    total = await session.scalar(select(func.coalesce(func.sum(LlmUsage.cost_usd), 0)).where(LlmUsage.created_at >= since))
    return float(total or 0)


async def ensure_budget(session: AsyncSession) -> None:
    budget = get_settings().claude_monthly_budget_usd
    spent = await spent_this_month(session)
    if spent >= budget:
        raise BudgetExceeded(f"Claude budget for this month used up: ${spent:.2f} of ${budget:.2f}")


async def record_usage(session: AsyncSession, feature: str, model: str, usage: Usage) -> float:
    cost = cost_usd(model, usage)
    session.add(
        LlmUsage(
            created_at=datetime.now(UTC),
            feature=feature,
            model=model,
            input_tokens=usage.input_tokens + (usage.cache_creation_input_tokens or 0) + (usage.cache_read_input_tokens or 0),
            output_tokens=usage.output_tokens,
            web_searches=web_searches(usage),
            cost_usd=cost,
        )
    )
    await session.commit()
    logger.info("Claude %s (%s): %s in / %s out tokens, %s searches, $%.4f", feature, model,
                usage.input_tokens, usage.output_tokens, web_searches(usage), cost)
    return cost
