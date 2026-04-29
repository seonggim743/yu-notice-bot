"""HTTP client for Canvas LMS REST API.

Handles three concerns the rest of the integration shouldn't think about:
- Bearer token auth
- Pagination via the `Link: <...>; rel="next"` response header
- Soft rate limiting (sleep when X-Rate-Limit-Remaining drops low) and
  hard 429 with Retry-After / 401 / 403 surfacing
"""
import asyncio
import json
import re
from typing import Any, Dict, List, Optional

import aiohttp

from core.error_notifier import ErrorNotifier, ErrorSeverity
from core.exceptions import (
    CanvasAuthException,
    CanvasRateLimitException,
    NetworkException,
)
from core.logger import get_logger
from models.canvas import (
    CanvasAnnouncement,
    CanvasAssignment,
    CanvasCourse,
    CanvasSubmission,
)

logger = get_logger(__name__)


class CanvasClient:
    """Async client over Canvas LMS REST endpoints.

    The session is injected so the caller controls connection-pool lifetime.
    """

    # Soft threshold: when remaining quota dips below this we pre-emptively sleep.
    LOW_QUOTA_THRESHOLD = 50.0
    LOW_QUOTA_SLEEP_SECONDS = 2.0

    PAGE_SIZE = 50  # Canvas accepts per_page up to 100; 50 is a safe default.

    def __init__(
        self,
        api_url: str,
        api_token: str,
        session: aiohttp.ClientSession,
        error_notifier: Optional[ErrorNotifier] = None,
        max_timeout_retries: int = 2,
        retry_base_delay: float = 1.0,
    ):
        self.api_url = api_url.rstrip("/")
        self.api_token = api_token
        self.session = session
        self.error_notifier = error_notifier
        self.max_timeout_retries = max_timeout_retries
        self.retry_base_delay = retry_base_delay

    # ---------- Public endpoints ----------

    async def get_active_courses(self) -> List[CanvasCourse]:
        """GET /api/v1/courses?enrollment_state=active"""
        rows = await self._paginated(
            "/api/v1/courses",
            params={"enrollment_state": "active", "per_page": self.PAGE_SIZE},
        )
        return [CanvasCourse.model_validate(r) for r in rows]

    async def get_assignments(self, course_id: int) -> List[CanvasAssignment]:
        """GET /api/v1/courses/:id/assignments"""
        rows = await self._paginated(
            f"/api/v1/courses/{course_id}/assignments",
            params={"per_page": self.PAGE_SIZE},
        )
        result = []
        for r in rows:
            r.setdefault("course_id", course_id)
            result.append(CanvasAssignment.model_validate(r))
        return result

    async def get_announcements(
        self, course_ids: List[int]
    ) -> List[CanvasAnnouncement]:
        """GET /api/v1/announcements?context_codes[]=course_X&context_codes[]=course_Y"""
        if not course_ids:
            return []
        params = [("per_page", str(self.PAGE_SIZE))]
        for cid in course_ids:
            params.append(("context_codes[]", f"course_{cid}"))
        rows = await self._paginated("/api/v1/announcements", params=params)
        result = []
        for r in rows:
            ctx = r.get("context_code", "")
            if ctx.startswith("course_"):
                try:
                    r["course_id"] = int(ctx.split("_", 1)[1])
                except ValueError:
                    pass
            result.append(CanvasAnnouncement.model_validate(r))
        return result

    async def get_submissions(self, course_id: int) -> List[CanvasSubmission]:
        """GET /api/v1/courses/:id/students/submissions?student_ids[]=self"""
        rows = await self._paginated(
            f"/api/v1/courses/{course_id}/students/submissions",
            params=[
                ("student_ids[]", "self"),
                ("per_page", str(self.PAGE_SIZE)),
            ],
        )
        result = []
        for r in rows:
            r.setdefault("course_id", course_id)
            result.append(CanvasSubmission.model_validate(r))
        return result

    # ---------- Internal request plumbing ----------

    async def _paginated(
        self, endpoint: str, params: Any = None
    ) -> List[Dict[str, Any]]:
        """Walk Canvas pagination via the Link: rel=next header."""
        url: Optional[str] = self._build_url(endpoint)
        first_pass_params = params
        results: List[Dict[str, Any]] = []
        while url:
            data, next_url = await self._request("GET", url, params=first_pass_params)
            first_pass_params = None  # query string already encoded into next_url
            if isinstance(data, list):
                results.extend(data)
            url = next_url
        return results

    async def _request(
        self,
        method: str,
        url: str,
        params: Any = None,
    ):
        """Single Canvas request with timeout retry."""
        last_timeout: Optional[BaseException] = None
        for attempt in range(self.max_timeout_retries + 1):
            try:
                return await self._request_once(method, url, params=params)
            except asyncio.TimeoutError as e:
                last_timeout = e
                if attempt >= self.max_timeout_retries:
                    break
                delay = self.retry_base_delay * (2**attempt)
                logger.warning(
                    f"[CANVAS] Request timed out for {url}. "
                    f"Retrying in {delay:.1f}s "
                    f"({attempt + 1}/{self.max_timeout_retries})."
                )
                await asyncio.sleep(delay)

        raise NetworkException(
            "Canvas request timed out",
            details={"url": url, "attempts": self.max_timeout_retries + 1},
        ) from last_timeout

    async def _request_once(
        self,
        method: str,
        url: str,
        params: Any = None,
    ):
        """Single Canvas request attempt. Returns (json_body, next_url_or_None)."""
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json+canvas-string-ids, application/json",
        }

        try:
            async with self.session.request(
                method,
                url,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                # 401 / 403 / 429 — explicit handling
                if resp.status == 401:
                    await self._notify_auth_failure(url, resp.status)
                    raise CanvasAuthException(
                        "Canvas API token is invalid or expired (401). Check CANVAS_API_TOKEN.",
                        details={"url": url},
                    )
                if resp.status == 403:
                    raise CanvasAuthException(
                        "Canvas API token lacks required permission (403)",
                        details={"url": url},
                    )
                if resp.status == 429:
                    retry_after = float(resp.headers.get("Retry-After", "1") or 1)
                    logger.warning(
                        f"[CANVAS] 429 rate limit hit. Sleeping {retry_after}s."
                    )
                    await asyncio.sleep(retry_after)
                    raise CanvasRateLimitException(
                        "Canvas API rate-limited",
                        details={"retry_after": retry_after},
                    )
                if resp.status >= 400:
                    text = await resp.text()
                    raise NetworkException(
                        f"Canvas API error {resp.status}",
                        details={"url": url, "body": text[:300]},
                    )

                # Soft pre-emptive sleep when quota drops low
                remaining = self._safe_float(resp.headers.get("X-Rate-Limit-Remaining"))
                if remaining is not None and remaining < self.LOW_QUOTA_THRESHOLD:
                    logger.info(
                        f"[CANVAS] X-Rate-Limit-Remaining={remaining:.1f} below "
                        f"threshold {self.LOW_QUOTA_THRESHOLD}; sleeping "
                        f"{self.LOW_QUOTA_SLEEP_SECONDS}s."
                    )
                    await asyncio.sleep(self.LOW_QUOTA_SLEEP_SECONDS)

                next_url = self._parse_next_link(resp.headers.get("Link", ""))
                body = await self._json_body(resp, url)
                return body, next_url

        except asyncio.TimeoutError:
            raise
        except aiohttp.ClientError as e:
            raise NetworkException(
                f"Canvas request failed: {e}", details={"url": url}
            ) from e

    async def _json_body(self, resp: aiohttp.ClientResponse, url: str) -> Any:
        """Decode JSON and surface Canvas maintenance/non-JSON pages clearly."""
        content_type = resp.headers.get("Content-Type", "")
        if "json" not in content_type.lower():
            text = await resp.text()
            raise NetworkException(
                "Canvas API returned a non-JSON response",
                details={
                    "url": url,
                    "content_type": content_type,
                    "body": text[:300],
                },
            )

        try:
            return await resp.json(content_type=None)
        except json.JSONDecodeError as e:
            text = await resp.text()
            raise NetworkException(
                "Canvas API returned invalid JSON",
                details={"url": url, "body": text[:300]},
            ) from e

    async def _notify_auth_failure(self, url: str, status: int) -> None:
        """Notify operations when Canvas auth cannot continue."""
        if self.error_notifier is None:
            return
        try:
            await self.error_notifier.send_critical_error(
                "Canvas API token is invalid or expired. Check CANVAS_API_TOKEN.",
                context={"url": url, "status": status},
                severity=ErrorSeverity.HIGH,
            )
        except Exception as e:
            logger.error(f"[CANVAS] Failed to send auth failure alert: {e}")

    # ---------- Helpers ----------

    def _build_url(self, endpoint: str) -> str:
        if endpoint.startswith("http"):
            return endpoint
        return f"{self.api_url}{endpoint}"

    @staticmethod
    def _parse_next_link(link_header: str) -> Optional[str]:
        """Extract the rel="next" URL from RFC 5988 Link header."""
        if not link_header:
            return None
        # Each segment looks like: <https://...>; rel="next"
        for part in link_header.split(","):
            match = re.match(r"\s*<([^>]+)>\s*;\s*rel=\"?next\"?", part)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
