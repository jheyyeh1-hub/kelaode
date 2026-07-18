"""Fetch high-star GitHub projects useful for an A-share quant stack.

The script uses GitHub's public Search/Repository APIs and prints a compact JSON
snapshot. It is intentionally dependency-free so it can run in a clean research
environment before the trading stack has infrastructure dependencies.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

QUERIES = [
    "A股 量化 language:Python stars:>500",
    "stock quant backtest language:Python stars:>1000",
    "vnpy stars:>1000",
    "qmt xtquant language:Python stars:>50",
]


def github_get(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "kelaode-research"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def search_repositories() -> list[dict[str, Any]]:
    seen: set[str] = set()
    projects: list[dict[str, Any]] = []
    for query in QUERIES:
        url = "https://api.github.com/search/repositories?sort=stars&order=desc&per_page=10&q="
        data = github_get(url + urllib.parse.quote(query))
        for item in data.get("items", []):
            full_name = item["full_name"]
            if full_name in seen:
                continue
            seen.add(full_name)
            projects.append(
                {
                    "repo": full_name,
                    "stars": item["stargazers_count"],
                    "forks": item["forks_count"],
                    "updated_at": item["updated_at"],
                    "license": item["license"]["spdx_id"] if item.get("license") else None,
                    "url": item["html_url"],
                    "description": item.get("description"),
                }
            )
    return sorted(projects, key=lambda project: project["stars"], reverse=True)


if __name__ == "__main__":
    print(json.dumps(search_repositories(), ensure_ascii=False, indent=2))
