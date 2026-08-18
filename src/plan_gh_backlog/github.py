from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable


class GitHubError(RuntimeError):
    pass


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


class UrllibTransport:
    def __call__(self, url: str, method: str, headers: dict[str, str], body: bytes | None) -> Response:
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return Response(response.status, dict(response.headers.items()), response.read())
        except urllib.error.HTTPError as error:
            return Response(error.code, dict(error.headers.items()), error.read())


def auth_token() -> str:
    token = os.environ.get("GH_TOKEN")
    if token:
        return token
    result = subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, check=False, timeout=15
    )
    token = result.stdout.strip()
    if result.returncode or not token:
        raise GitHubError("GitHub authentication unavailable; set GH_TOKEN or run 'gh auth login'")
    return token


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        transport: Callable[[str, str, dict[str, str], bytes | None], Response] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        max_retries: int = 5,
        api_url: str = "https://api.github.com",
    ) -> None:
        self._token = token or auth_token()
        self._transport = transport or UrllibTransport()
        self._sleep = sleeper
        self._clock = clock
        self._max_retries = max_retries
        self._api_url = api_url.rstrip("/")

    def request(self, method: str, path: str, payload: dict | None = None) -> object:
        url = path if path.startswith("http") else self._api_url + path
        body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "plan-gh-backlog/1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(self._max_retries + 1):
            response = self._transport(url, method, headers, body)
            text = response.body.decode("utf-8", errors="replace")
            if 200 <= response.status < 300:
                return json.loads(text) if text else None
            if attempt < self._max_retries and self._retryable(response, text):
                self._sleep(self._delay(response, attempt))
                continue
            message = text
            try:
                message = json.loads(text).get("message", text)
            except json.JSONDecodeError:
                pass
            raise GitHubError(f"GitHub API {method} {urllib.parse.urlsplit(url).path} returned {response.status}: {message}")
        raise AssertionError("retry loop exhausted")

    def paginate(self, path: str) -> list[dict]:
        separator = "&" if "?" in path else "?"
        url = self._api_url + path + f"{separator}per_page=100"
        results: list[dict] = []
        while url:
            response_data, headers = self._request_page(url)
            if not isinstance(response_data, list):
                raise GitHubError(f"expected paginated list from {path}")
            results.extend(response_data)
            url = _next_link(headers.get("Link") or headers.get("link"))
        return results

    def _request_page(self, url: str) -> tuple[object, dict[str, str]]:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "plan-gh-backlog/1",
        }
        for attempt in range(self._max_retries + 1):
            response = self._transport(url, "GET", headers, None)
            text = response.body.decode("utf-8", errors="replace")
            if 200 <= response.status < 300:
                return (json.loads(text) if text else None), response.headers
            if attempt < self._max_retries and self._retryable(response, text):
                self._sleep(self._delay(response, attempt))
                continue
            raise GitHubError(f"GitHub API GET {urllib.parse.urlsplit(url).path} returned {response.status}: {text}")
        raise AssertionError("retry loop exhausted")

    @staticmethod
    def _retryable(response: Response, text: str) -> bool:
        remaining = response.headers.get("X-RateLimit-Remaining") or response.headers.get("x-ratelimit-remaining")
        secondary = "secondary rate limit" in text.lower() or "abuse detection" in text.lower()
        return response.status in {429, 500, 502, 503, 504} or (response.status == 403 and (remaining == "0" or secondary))

    def _delay(self, response: Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), 120.0)
        reset = response.headers.get("X-RateLimit-Reset") or response.headers.get("x-ratelimit-reset")
        if reset and reset.isdigit():
            return min(max(float(reset) - self._clock() + 1, 1.0), 120.0)
        return min(2.0**attempt, 60.0)


def _next_link(value: str | None) -> str | None:
    if not value:
        return None
    for segment in value.split(","):
        match = re.match(r'\s*<([^>]+)>;\s*rel="([^"]+)"', segment)
        if match and match.group(2) == "next":
            return match.group(1)
    return None
