"""``web_fetch`` — fetch a URL with optional HTML extraction."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, Field

from pyharness import Tool, ToolContext, ToolError


class WebFetchArgs(BaseModel):
    url: str = Field(description="HTTP(S) URL to fetch.")
    timeout: int = Field(default=30, ge=1, le=120)


class WebFetchTool(Tool):
    name = "web_fetch"
    description = (
        "Fetch a URL via HTTPS. For HTML, extracts the main text. Returns "
        "metadata (status, content type, fetched_at) and the body."
    )
    args_schema = WebFetchArgs

    async def execute(self, args: WebFetchArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        settings = ctx.settings
        timeout = args.timeout or (
            getattr(settings, "fetch_timeout_seconds", 30) if settings else 30
        )

        allowlist = _list_setting(settings, "fetch_allowlist")
        blocklist = _list_setting(settings, "fetch_blocklist")
        host = _host_of(args.url)

        if blocklist and any(_host_matches(host, b) for b in blocklist):
            return f"Blocked by fetch_blocklist: {host}"
        if allowlist and not any(_host_matches(host, a) for a in allowlist):
            return f"Not on fetch_allowlist: {host}"

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(args.url, headers={"User-Agent": "pyharness/0.1"})
        except httpx.HTTPError as exc:
            raise ToolError(f"web_fetch error: {exc}") from exc

        ctype = resp.headers.get("content-type", "")
        body = resp.text
        if "html" in ctype.lower():
            body = _extract_html(body)

        meta = (
            f"url: {args.url}\n"
            f"status: {resp.status_code}\n"
            f"content_type: {ctype}\n"
            f"fetched_at: {datetime.now(UTC).isoformat()}\n"
            f"--- body ---\n"
        )
        # 4xx/5xx are failures from the agent's perspective: the URL didn't
        # resolve to the resource being asked for. Raising as ToolError
        # surfaces ok=False to the harness, which lets the circuit breaker
        # see consecutive bad-URL guesses as the failure pattern they are.
        # (HTTP-200 pages with a 404 *body*, e.g. soft-404s, still slip
        # through — that's a model-judgment problem we don't fix here.)
        if resp.status_code >= 400:
            raise ToolError(meta + body)
        return meta + body


def _list_setting(settings: object, name: str) -> list[str]:
    value = getattr(settings, name, None) if settings else None
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def _host_of(url: str) -> str:
    m = re.match(r"^[a-zA-Z]+://([^/]+)", url)
    return m.group(1).lower() if m else url.lower()


def _host_matches(host: str, pattern: str) -> bool:
    pattern = pattern.lower().strip()
    if pattern.startswith("*."):
        return host == pattern[2:] or host.endswith("." + pattern[2:])
    return host == pattern


def _extract_html(html: str) -> str:
    try:
        import trafilatura  # type: ignore

        extracted = trafilatura.extract(html) or ""
        if extracted.strip():
            return extracted
    except Exception:
        pass
    # Fallback: strip tags crudely and truncate.
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:50_000]
