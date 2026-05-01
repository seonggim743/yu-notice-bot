import urllib.parse

from aiohttp import MultipartWriter

from services.notification.base import BaseNotifier
from services.notification.discord import (
    DiscordNotifier,
    _discord_code_block,
    _discord_updated_summary,
)


def test_file_part_includes_utf8_filename_star_for_korean_names():
    writer = MultipartWriter("form-data")
    filename = "강의자료 원본.pdf"

    BaseNotifier()._add_file_part(writer, "files[0]", b"data", filename)

    part = writer._parts[0][0]
    disposition = part.headers["Content-Disposition"]

    assert f'filename="{filename}"' in disposition
    assert f"filename*=UTF-8''{urllib.parse.quote(filename)}" in disposition


def test_discord_reply_payload_references_original_message():
    payload = DiscordNotifier._discord_reply_payload(
        "1234567890", content="📎 [원본] 강의자료.pdf (10KB)"
    )

    assert payload["content"].startswith("📎 [원본]")
    assert payload["message_reference"] == {
        "message_id": "1234567890",
        "fail_if_not_exists": False,
    }
    assert payload["allowed_mentions"] == {"replied_user": False}


def test_discord_code_block_wraps_modified_details():
    block = _discord_code_block("🔴 이전\n🟢 이후")

    assert block == "```text\n🔴 이전\n🟢 이후\n```"


def test_discord_updated_summary_adds_bullets():
    summary = _discord_updated_summary("첫 번째 요약\n- 이미 있는 요약")

    assert summary == "- 첫 번째 요약\n- 이미 있는 요약"
