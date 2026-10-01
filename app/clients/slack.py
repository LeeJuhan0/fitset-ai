import asyncio
import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger("fitset")

MAX_DETAIL_CHARS = 1500


def error_text(code: str, method: str, path: str, exc: BaseException, trace_id: str) -> str:
    """백엔드 에러 알림과 같은 양식 본문"""
    root = exc.__cause__ or exc
    detail = f"{type(root).__module__}.{type(root).__qualname__}: {root}"[:MAX_DETAIL_CHARS]
    return f":rotating_light: *{code}* `{method} {path}`\n```{detail}```\ntrace_id: {trace_id}"


def _post(url: str, text: str) -> None:
    """웹훅 전송, 실패는 로그만"""
    try:
        httpx.post(url, json={"text": text}, timeout=3).raise_for_status()
    except httpx.HTTPError:
        logger.warning("slack error notify failed", exc_info=True)


def notify_error(code: str, method: str, path: str, exc: BaseException, trace_id: str) -> None:
    """5xx 에러를 슬랙 에러 채널로 비동기 전송"""
    url = get_settings().slack_error_webhook_url
    if not url:
        return
    text = error_text(code, method, path, exc, trace_id)
    asyncio.get_running_loop().run_in_executor(None, _post, url, text)
