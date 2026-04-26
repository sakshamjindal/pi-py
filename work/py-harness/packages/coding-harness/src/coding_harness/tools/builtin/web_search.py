"""``web_search`` — search the web via a configurable provider.

Provider is selected via settings (``search_provider``). The API key is
read from the env var named in ``search_api_key_env``. If no provider is
configured, the tool returns an actionable error so the agent can stop
calling it.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from pydantic import BaseModel, Field

from pyharness import Tool, ToolContext


class WebSearchArgs(BaseModel):
    query: str = Field(description="Search query.")
    max_results: int = Field(default=5, ge=1, le=20)


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the web. Returns `title — url\\nsnippet` blocks. Configure "
        "the provider in settings.json (`search_provider`)."
    )
    args_schema = WebSearchArgs

    async def execute(self, args: WebSearchArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        settings = ctx.settings
        provider = (getattr(settings, "search_provider", None) if settings else None) or "brave"
        env_key = (
            getattr(settings, "search_api_key_env", None) if settings else None
        ) or "BRAVE_API_KEY"
        api_key = os.environ.get(env_key)
        if not api_key:
            return (
                f"web_search not configured: set ${env_key} or change "
                f"`search_provider` in settings.json."
            )

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                if provider == "brave":
                    return await _brave(client, api_key, args)
                if provider == "tavily":
                    return await _tavily(client, api_key, args)
                if provider == "exa":
                    return await _exa(client, api_key, args)
                return f"Unknown search provider: {provider!r}"
        except httpx.HTTPError as exc:
            return f"web_search HTTP error: {exc}"


async def _brave(client: httpx.AsyncClient, key: str, args: WebSearchArgs) -> str:
    resp = await client.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": args.query, "count": args.max_results},
        headers={"Accept": "application/json", "X-Subscription-Token": key},
    )
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    web = (data.get("web") or {}).get("results") or []
    return _format_results(
        [(r.get("title", ""), r.get("url", ""), r.get("description", "")) for r in web]
    )


async def _tavily(client: httpx.AsyncClient, key: str, args: WebSearchArgs) -> str:
    resp = await client.post(
        "https://api.tavily.com/search",
        json={"api_key": key, "query": args.query, "max_results": args.max_results},
    )
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    results = data.get("results") or []
    return _format_results(
        [(r.get("title", ""), r.get("url", ""), r.get("content", "")) for r in results]
    )


async def _exa(client: httpx.AsyncClient, key: str, args: WebSearchArgs) -> str:
    resp = await client.post(
        "https://api.exa.ai/search",
        json={"query": args.query, "numResults": args.max_results},
        headers={"x-api-key": key},
    )
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    results = data.get("results") or []
    return _format_results(
        [
            (r.get("title", ""), r.get("url", ""), r.get("text", "") or r.get("snippet", ""))
            for r in results
        ]
    )


def _format_results(triples: list[tuple[str, str, str]]) -> str:
    if not triples:
        return "No results."
    return "\n\n".join(f"{t} — {u}\n{s}" for (t, u, s) in triples)
