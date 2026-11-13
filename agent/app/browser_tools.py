"""Bounded Playwright browser tools with public-network-only navigation."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_PAGE_CHARS = 12_000
MAX_LINKS = 40


class BrowserToolError(ValueError):
    """A safe, user-facing browser-tool error."""


async def validate_public_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise BrowserToolError("browser URL is malformed") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise BrowserToolError("browser URL must be an HTTP(S) URL without embedded credentials")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
        raise BrowserToolError("browser access to local hostnames is blocked")
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise BrowserToolError("browser access to private or reserved IP addresses is blocked")
    else:
        try:
            results = await asyncio.get_running_loop().getaddrinfo(
                hostname, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM
            )
        except (OSError, ValueError) as exc:
            raise BrowserToolError("browser hostname could not be resolved") from exc
        addresses = {ipaddress.ip_address(item[4][0]) for item in results}
        if not addresses or any(not address.is_global for address in addresses):
            raise BrowserToolError("browser access to hosts resolving to private or reserved IP addresses is blocked")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def _public_url_without_query(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


async def browse_url(url: str, follow_link_text: str | None = None) -> dict[str, object]:
    """Open a public web page and return bounded text and links, optionally following one link."""
    safe_url = await validate_public_url(url)
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise BrowserToolError("browser support is not installed; install requirements and run `playwright install chromium`") from exc

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=True)
        except Exception as exc:
            raise BrowserToolError("Chromium is unavailable; run `playwright install chromium`") from exc
        try:
            context = await browser.new_context(
                java_script_enabled=True,
                accept_downloads=False,
                service_workers="block",
            )
            page = await context.new_page()
            page.set_default_timeout(5_000)

            async def block_private_requests(route, request) -> None:
                if request.resource_type in {"image", "media", "font"}:
                    await route.abort()
                    return
                try:
                    await validate_public_url(request.url)
                except BrowserToolError:
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", block_private_requests)
            response = await page.goto(safe_url, wait_until="domcontentloaded", timeout=15_000)
            if follow_link_text:
                await page.get_by_role("link", name=follow_link_text, exact=True).first.click(timeout=5_000)
                await page.wait_for_load_state("domcontentloaded", timeout=10_000)
            title = (await page.title())[:500]
            text = (await page.locator("body").inner_text(timeout=5_000))[:MAX_PAGE_CHARS]
            links = []
            anchors = page.locator("a[href]")
            for index in range(min(await anchors.count(), MAX_LINKS)):
                anchor = anchors.nth(index)
                href = await anchor.get_attribute("href")
                label = (await anchor.inner_text(timeout=1_000)).strip()
                if href and label:
                    absolute = urlsplit(urljoin(page.url, href))
                    if absolute.scheme in {"http", "https"} and absolute.hostname and not absolute.username and not absolute.password:
                        links.append({"text": label[:200], "url": _public_url_without_query(absolute.geturl())})
            return {
                "url": _public_url_without_query(page.url),
                "title": title,
                "status": response.status if response else None,
                "text": text,
                "links": links,
                "truncated": len(text) >= MAX_PAGE_CHARS,
            }
        except BrowserToolError:
            raise
        except Exception as exc:
            raise BrowserToolError("browser could not complete the requested navigation") from exc
        finally:
            await browser.close()
