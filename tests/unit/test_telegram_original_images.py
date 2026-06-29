import importlib
import sys
import types

import pytest
from aiohttp import MultipartWriter


class _BaseNotifier:
    def _add_text_part(self, writer: MultipartWriter, name, value):
        part = writer.append(str(value))
        part.set_content_disposition("form-data", name=name)

    def _add_file_part(
        self,
        writer: MultipartWriter,
        field_name,
        file_data,
        filename,
        content_type="application/octet-stream",
    ):
        part = writer.append(file_data, {"Content-Type": content_type})
        part.headers[
            "Content-Disposition"
        ] = f'form-data; name="{field_name}"; filename="{filename}"'


@pytest.fixture
def telegram_module(monkeypatch):
    fake_config = types.ModuleType("core.config")
    fake_config.settings = types.SimpleNamespace(
        TELEGRAM_TOKEN="token",
        TELEGRAM_CHAT_ID="chat",
        TELEGRAM_TOPIC_MAP={},
        USER_AGENT="test-agent",
    )
    monkeypatch.setitem(sys.modules, "core.config", fake_config)

    fake_logger = types.ModuleType("core.logger")
    fake_logger.get_logger = lambda _name: types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    monkeypatch.setitem(sys.modules, "core.logger", fake_logger)

    fake_notice = types.ModuleType("models.notice")
    fake_notice.Notice = object
    monkeypatch.setitem(sys.modules, "models.notice", fake_notice)

    fake_downloader = types.ModuleType("services.file.attachment_downloader")
    fake_downloader.AttachmentDownloader = lambda *args, **kwargs: object()
    monkeypatch.setitem(
        sys.modules, "services.file.attachment_downloader", fake_downloader
    )

    fake_image = types.ModuleType("services.file.image")
    fake_image.ImageHandler = lambda *args, **kwargs: object()
    monkeypatch.setitem(sys.modules, "services.file.image", fake_image)

    fake_dev = types.ModuleType("services.notification.dev_notifier")
    fake_dev.DevNotifier = lambda *args, **kwargs: object()
    monkeypatch.setitem(sys.modules, "services.notification.dev_notifier", fake_dev)

    fake_base = types.ModuleType("services.notification.base")
    fake_base.BaseNotifier = _BaseNotifier
    fake_base.NotificationChannel = object
    monkeypatch.setitem(sys.modules, "services.notification.base", fake_base)

    fake_diff = types.ModuleType("services.notification.diff_chunker")
    fake_diff.split_diff = lambda text, _limit: [text]
    monkeypatch.setitem(sys.modules, "services.notification.diff_chunker", fake_diff)

    fake_formatters = types.ModuleType("services.notification.formatters")
    fake_formatters.create_telegram_message = (
        lambda notice, is_new, modified_reason, changes: "message"
    )
    fake_formatters.format_telegram_revised_body_quote_parts = lambda text: [text]
    monkeypatch.setitem(
        sys.modules, "services.notification.formatters", fake_formatters
    )

    module_name = "services.notification.telegram"
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)

    yield module

    sys.modules.pop(module_name, None)


@pytest.mark.asyncio
async def test_original_content_images_are_sent_as_documents(telegram_module):
    notifier = telegram_module.TelegramNotifier.__new__(
        telegram_module.TelegramNotifier
    )
    notifier.chat_id = "chat"
    calls = []

    async def fake_api(session, method, payload=None, data=None, retries=3):
        calls.append({"method": method, "data": data})
        return {"ok": True}

    notifier._send_telegram_api = fake_api

    await notifier._send_original_content_image_documents(
        session=object(),
        content_images=[
            {"filename": "image_0.jpg", "original_data": b"original-image"}
        ],
        topic_id=123,
        reply_to_message_id=456,
    )

    assert [call["method"] for call in calls] == ["sendDocument"]
    form = calls[0]["data"]
    dispositions = [
        part.headers["Content-Disposition"] for part, *_ in form._parts
    ]
    assert any(
        'name="document"; filename="original_image_0.jpg"' in item
        for item in dispositions
    )
    assert any('name="reply_to_message_id"' in item for item in dispositions)
    assert any('name="message_thread_id"' in item for item in dispositions)


@pytest.mark.asyncio
async def test_oversized_original_content_images_are_skipped(telegram_module):
    notifier = telegram_module.TelegramNotifier.__new__(
        telegram_module.TelegramNotifier
    )
    notifier.chat_id = "chat"
    calls = []

    async def fake_api(session, method, payload=None, data=None, retries=3):
        calls.append(method)
        return {"ok": True}

    notifier._send_telegram_api = fake_api
    oversized = b"x" * (telegram_module.constants.TELEGRAM_FILE_SIZE_LIMIT + 1)

    await notifier._send_original_content_image_documents(
        session=object(),
        content_images=[{"filename": "image_0.jpg", "original_data": oversized}],
        topic_id=None,
        reply_to_message_id=456,
    )

    assert calls == []
