from functools import lru_cache
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import get_settings


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500


class SejmClient:
    """Thin async client for the raw api.sejm.gov.pl endpoints we mirror."""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def proceedings(self, term: int) -> list[dict[str, Any]]:
        return await self._get(f"/term{term}/proceedings")

    async def votings_per_day(self, term: int) -> list[dict[str, Any]]:
        """Number of votings on each sitting day of the term."""
        return await self._get(f"/term{term}/votings")

    async def votings(self, term: int, sitting: int) -> list[dict[str, Any]]:
        return await self._get(f"/term{term}/votings/{sitting}")

    async def voting(self, term: int, sitting: int, number: int) -> dict[str, Any]:
        """Single voting including the per-MP `votes` list."""
        return await self._get(f"/term{term}/votings/{sitting}/{number}")

    async def process(self, term: int, print_number: str) -> dict[str, Any] | None:
        """Legislative process started by the given print, or None if the print does not start one."""
        return await self._get_or_none(f"/term{term}/processes/{print_number}")

    async def mp(self, term: int, mp_id: int) -> dict[str, Any] | None:
        """MP profile, or None if there is no MP with this id in the term."""
        return await self._get_or_none(f"/term{term}/MP/{mp_id}")

    async def _get_or_none(self, path: str) -> Any:
        try:
            return await self._get(path)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _get(self, path: str) -> Any:
        response = await self._http.get(path)
        response.raise_for_status()
        return response.json()


@lru_cache
def get_sejm_client() -> SejmClient:
    return SejmClient(get_settings().sejm_api_url)
