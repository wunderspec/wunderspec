"""HTTPS-only service client using the system trust store and an optional extra CA."""

import http.client
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, cast
from uuid import UUID

from wunderspec.remote.config import check_service_url

MAX_RESPONSE = 1024 * 1024
MAX_ARTIFACT = 210 * 1024 * 1024
MEDIA_TYPE = "application/vnd.wunderspec.transfer+json"
_MESSAGES = {
    "unauthorized": "authentication failed; check your SaaS token",
    "rate_limited": "request rate limit reached",
    "too_many_jobs": "job quota reached",
    "service_busy": "service is busy",
    "service_unavailable": "service is unavailable",
    "unsupported_format_version": "service does not support this transfer format version",
    "invalid_document": "service rejected the transfer document",
    "invalid_request": "service rejected the request",
    "not_found": "job was not found",
    "expired": "job artifact has expired",
    "not_ready": "job artifact is not ready",
    "payload_too_large": "transfer document is too large",
    "unsupported_media_type": "service rejected the document media type",
}


class ServiceError(Exception):
    def __init__(
        self,
        code: str,
        status: int,
        retry_after: str | None = None,
        supported_versions: list[str] | None = None,
    ):
        self.code, self.status = code, status
        self.retry_after = retry_after
        # The transfer format versions the service accepts, for unsupported_format_version.
        self.supported_versions = supported_versions
        message = _MESSAGES.get(code, "service refused the request")
        if retry_after is not None:
            message += f"; Retry-After: {retry_after}"
        super().__init__(message)


class NetworkError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # A redirect must never forward the bearer token to another origin or to HTTP.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(
        self, service: str, token: str, ca_file: str | os.PathLike[str] | None = None
    ):
        check_service_url(service)
        try:
            context = ssl.create_default_context()
            if ca_file is not None:
                context.load_verify_locations(cafile=ca_file)
        except (OSError, ValueError, ssl.SSLError):
            raise ValueError("invalid service URL or CA file") from None
        self.service = service.rstrip("/")
        self._token = token
        self._opener = urllib.request.build_opener(
            _NoRedirect(), urllib.request.HTTPSHandler(context=context)
        )

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        *,
        timeout: float = 30,
        artifact: bool = False,
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/zip" if artifact else "application/json",
        }
        if body is not None:
            headers["Content-Type"] = MEDIA_TYPE
        request = urllib.request.Request(
            self.service + path, data=body, headers=headers, method=method
        )
        limit = MAX_ARTIFACT if artifact else MAX_RESPONSE
        try:
            with self._opener.open(request, timeout=max(0.001, timeout)) as response:
                data = response.read(limit + 1)
        except urllib.error.HTTPError as exc:
            with exc:
                supported: list[str] | None = None
                try:
                    error = json.loads(exc.read(MAX_RESPONSE + 1))
                    code = (
                        error.get("error", "internal_error")
                        if isinstance(error, dict)
                        else "internal_error"
                    )
                    versions = (
                        error.get("supported_versions")
                        if isinstance(error, dict)
                        else None
                    )
                    if isinstance(versions, list) and all(
                        isinstance(v, str)
                        and re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,4}", v)
                        for v in versions
                    ):
                        supported = versions[:16]
                except (ValueError, UnicodeError):
                    code = "internal_error"
                retry = exc.headers.get("Retry-After")
                if retry is not None and (
                    not re.fullmatch(
                        r"[0-9]{1,10}|[A-Za-z]{3}, [0-9]{2} [A-Za-z]{3} [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT",
                        retry,
                    )
                    or len(retry) > 128
                    or any(ord(c) < 32 or ord(c) > 126 for c in retry)
                ):
                    retry = None
            raise ServiceError(
                code if isinstance(code, str) else "internal_error",
                exc.code,
                retry,
                supported,
            ) from None
        except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
            raise NetworkError("network or TLS request failed") from None
        if len(data) > limit:
            raise NetworkError("service response exceeds size limit")
        if artifact:
            return data
        try:
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, UnicodeError):
            raise NetworkError("invalid service response") from None

    def submit(
        self, document: dict, options: dict[str, str], *, timeout: float = 30
    ) -> str:
        body = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        result = self.request(
            "POST",
            "/v1/rust/jobs?" + urllib.parse.urlencode(options),
            body,
            timeout=timeout,
        )
        try:
            return str(UUID(result["job_id"]))
        except (KeyError, ValueError, TypeError, AttributeError):
            raise NetworkError("invalid job ID in service response") from None

    def job(self, job_id: str, *, timeout: float = 30) -> dict:
        return cast(dict, self.request("GET", f"/v1/jobs/{job_id}", timeout=timeout))

    def artifact(self, job_id: str, *, timeout: float = 30) -> bytes:
        return cast(
            bytes,
            self.request(
                "GET", f"/v1/jobs/{job_id}/artifact", timeout=timeout, artifact=True
            ),
        )
