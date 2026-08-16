import asyncio
import tempfile
import threading
import os

from telethon import TelegramClient, events

from base_module import BaseModule, Credential


def _resolve_peer(value: str):
    value = value.strip()
    if value.lstrip("-").isdigit():
        return int(value)
    return value.lstrip("@")


class Telegram(BaseModule):

    unique_id = "telegram_1"
    name = "Telegram"
    description = "Модуль для Telegram через личный аккаунт (MTProto), сообщения идут напрямую в личке, без бота"
    supports_files = True

    expected_credentials = [
        Credential("API ID", "api_id приложения с my.telegram.org (раздел API development tools)"),
        Credential("API Hash", "api_hash приложения с my.telegram.org"),
        Credential("Phone Number", "Номер телефона аккаунта в международном формате, например +79991234567"),
    ]

    client: TelegramClient = None

    class Sender(BaseModule.Sender):

        def __init__(self, credentials, user_id, client):
            super().__init__(credentials, user_id)
            self.client = client
            self.companion = _resolve_peer(user_id)

        def _run(self, coro):
            return asyncio.run_coroutine_threadsafe(coro, self.client.loop).result()

        def send(self, text: str):
            self._run(self.client.send_message(self.companion, text))

        def send_file(self, file_path: str):
            self._run(self.client.send_file(self.companion, file_path))

    class Listener(BaseModule.Listener):

        def __init__(self, credentials, ingester, file_ingester, user_id, client, stop_event):
            super().__init__(credentials, ingester, file_ingester, user_id, stop_event)
            self.client = client
            self.companion = _resolve_peer(user_id)

            self.client.add_event_handler(
                self._on_new_message,
                events.NewMessage(incoming=True, chats=self.companion),
            )

            threading.Thread(target=self._watch_stop, daemon=True).start()

        def _watch_stop(self):
            self.stop_event.wait()
            asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.client.loop)

        async def _on_new_message(self, event):
            try:
                if event.document:
                    local_path = await event.download_media(file=f"{tempfile.gettempdir()}{os.sep}")
                    if local_path:
                        self.file_ingester(local_path)
                elif event.raw_text:
                    self.ingester(event.raw_text)
            except Exception as e:
                self.logger.warning(f"Failed to process incoming message: {e}")

        def listen(self) -> str:
            pass

        def listen_file(self) -> str:
            pass

    def create_session(self, ingester: callable, file_ingester: callable = None):
        api_id, api_hash, phone = self.credentials

        session_name = f"cryptolayer_telegram_{phone.strip().lstrip('+')}"
        self.client = TelegramClient(session_name, int(api_id), api_hash)
        self.client.start(phone=phone)

        self.sender = self.Sender(self.credentials, self.user_id, self.client)
        self.listener = self.Listener(self.credentials, ingester, file_ingester, self.user_id, self.client, self.stop_event)

        threading.Thread(target=self.client.run_until_disconnected, daemon=True).start()
# main.py