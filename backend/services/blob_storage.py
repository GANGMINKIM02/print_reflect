from __future__ import annotations

"""Vercel Blob REST helper.

Uses Blob as a persistent store while allowing local fallback when unavailable.
"""

import logging
import mimetypes
from pathlib import Path

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    return settings.use_blob_storage


def public_url_for(pathname: str) -> str:
    return f"{settings.blob_base_url.rstrip('/')}/{pathname.lstrip('/')}"


def _auth_headers() -> dict[str, str]:
    token = settings.blob_read_write_token.strip()
    return {"Authorization": f"Bearer {token}"}


def _guess_content_type(pathname: str, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(pathname)
    return guessed or fallback


def put_bytes(pathname: str, content: bytes, *, content_type: str | None = None) -> str | None:
    """Upload bytes to Blob via REST PUT and return the public URL."""
    if not is_enabled():
        return None

    ctype = content_type or _guess_content_type(pathname)
    endpoint = public_url_for(pathname)
    headers = {
        **_auth_headers(),
        "x-content-type": ctype,
        "x-add-random-suffix": "false",
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.put(endpoint, headers=headers, content=content)
            response.raise_for_status()
            data = response.json()
            url = data.get("url")
            if isinstance(url, str) and url.strip():
                return url
            return endpoint
    except Exception as exc:  # noqa: BLE001
        logger.warning("Blob upload failed for %s: %s", pathname, exc)
    return None


def fetch_bytes_by_pathname(pathname: str) -> bytes | None:
    """Fetch bytes from deterministic public pathname URL."""
    if not is_enabled():
        return None
    url = public_url_for(pathname)
    try:
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            response = client.get(url)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.content
    except Exception as exc:  # noqa: BLE001
        logger.warning("Blob fetch failed for %s: %s", pathname, exc)
        return None


def download_to_path(url: str, target: Path) -> bool:
    """Download blob object to local path."""
    if not url:
        return False
    try:
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(response.content)
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Blob download failed for %s: %s", url, exc)
        return False


def list_urls(prefix: str) -> list[str]:
    """List blob URLs under prefix via REST API."""
    if not is_enabled():
        return []
    endpoint = f"{settings.blob_base_url.rstrip('/')}?prefix={prefix.lstrip('/')}"
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(endpoint, headers=_auth_headers())
            response.raise_for_status()
            data = response.json()
            blobs = data.get("blobs") if isinstance(data, dict) else []
            urls: list[str] = []
            if isinstance(blobs, list):
                for item in blobs:
                    if isinstance(item, dict):
                        url = item.get("url")
                        if isinstance(url, str) and url.strip():
                            urls.append(url)
            return urls
    except Exception as exc:  # noqa: BLE001
        logger.warning("Blob list failed for %s: %s", prefix, exc)
        return []


def delete_urls(urls: list[str]) -> bool:
    """Delete blob objects by URL via REST /delete endpoint."""
    valid = [u for u in urls if isinstance(u, str) and u.strip()]
    if not is_enabled() or not valid:
        return False
    endpoint = f"{settings.blob_base_url.rstrip('/')}/delete"
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                endpoint,
                headers={**_auth_headers(), "Content-Type": "application/json"},
                json={"urls": valid},
            )
            response.raise_for_status()
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Blob delete failed for %s urls: %s", len(valid), exc)
        return False


def delete_url(url: str) -> bool:
    return delete_urls([url])
