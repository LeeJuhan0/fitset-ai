import asyncio

from app.clients import slack
from app.core.config import Settings
from app.core.exceptions import AiUnavailableError


def test_에러_알림은_백엔드와_같은_양식에_원인_예외를_담는다():
    try:
        try:
            raise PermissionError("bedrock denied")
        except PermissionError as cause:
            raise AiUnavailableError() from cause
    except AiUnavailableError as exc:
        text = slack.error_text("AI_UNAVAILABLE", "POST", "/ai/v1/threads/1/messages", exc, "abc")

    assert text == (
        ":rotating_light: *AI_UNAVAILABLE* `POST /ai/v1/threads/1/messages`\n"
        "```builtins.PermissionError: bedrock denied```\n"
        "trace_id: abc"
    )


async def test_웹훅이_있으면_알림을_보내고_없으면_보내지_않는다(settings: Settings, monkeypatch):
    sent = []
    monkeypatch.setattr(slack, "_post", lambda url, text: sent.append(url))

    slack.notify_error("INTERNAL_ERROR", "GET", "/x", RuntimeError("boom"), "t")
    settings.slack_error_webhook_url = "https://hooks.example/abc"
    slack.notify_error("INTERNAL_ERROR", "GET", "/x", RuntimeError("boom"), "t")
    await asyncio.sleep(0.05)

    assert sent == ["https://hooks.example/abc"]
