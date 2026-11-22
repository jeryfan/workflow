from __future__ import annotations

import os
from typing import Any

import httpx

from api.core.graphon.http import HttpClientMaxRetriesExceededError, HttpResponse, HttpxHttpClient
from api.core.graphon.nodes.http_request.config import build_http_request_config
from api.core.graphon.nodes.http_request.node import HttpRequestNodeDependencies


class ProxyHttpClient(HttpxHttpClient):
    def __init__(self, proxy_url: str) -> None:
        if not proxy_url:
            raise ValueError("An HTTP request proxy is required")
        self._proxy_url = proxy_url

    def _request(self, method: str, url: str, *, max_retries: int = 0, **kwargs: Any) -> HttpResponse:
        request_kwargs = self._normalize_request_kwargs(kwargs)
        verify = request_kwargs.pop("verify", True)
        for attempt in range(max_retries + 1):
            try:
                with httpx.Client(proxy=self._proxy_url, trust_env=False, verify=verify) as client:
                    response = client.request(method, url, **request_kwargs)
                return HttpResponse.from_httpx(response)
            except httpx.RequestError as error:
                if attempt >= max_retries:
                    if max_retries == 0:
                        raise
                    raise HttpClientMaxRetriesExceededError(
                        f"Request failed after {max_retries + 1} attempts: {method} {url}"
                    ) from error
        raise RuntimeError("HTTP request retry loop exited without a result")


class UnsupportedFileManager:
    def download(self, _file, /) -> bytes:
        raise ValueError("File inputs are not supported")

    def create_file_by_raw(self, **_kwargs) -> Any:
        raise ValueError("File responses are not supported")

    def get_file_generator_by_tool_file_id(self, _file_id: str) -> tuple[None, None]:
        return None, None

    def build_from_mapping(self, *, mapping) -> Any:
        raise ValueError("File references are not supported")


def http_request_dependencies() -> HttpRequestNodeDependencies:
    files = UnsupportedFileManager()
    return HttpRequestNodeDependencies(
        tool_file_manager_factory=lambda: files,
        file_manager=files,
        file_reference_factory=files,
        http_client=ProxyHttpClient(os.environ.get("HTTP_REQUEST_PROXY", "http://ssrf-proxy:3128")),
    )


__all__ = ["build_http_request_config", "http_request_dependencies"]
