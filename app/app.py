#!/usr/bin/env python3

import json
import logging
import os
import re
import tempfile
from pathlib import Path

import cairosvg
from deltachat_rpc_client import (
    DeltaChat,
    EventType,
    JsonRpcError,
    Rpc,
    SpecialContactId,
)
from deltachat_rpc_client.const import MessageState

from httprint import Httprint

logger = logging.getLogger(__name__)

class DeltaRpcEventLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.msg != "account_id=%d got an event %s"


class HttprintDeltaChatBot:
    @staticmethod
    def parse_relays(value: str) -> tuple[str, ...]:
        if not value.strip():
            return ()
        relays = tuple(relay.strip().lower() for relay in value.split(","))
        label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        if any(not relay or len(relay) > 253 or not re.fullmatch(
            rf"{label}(?:\.{label})+", relay, flags=re.ASCII
        ) for relay in relays):
            raise ValueError("CHATMAIL_RELAYS must contain comma-separated relay hostnames")
        return tuple(dict.fromkeys(relays))

    @staticmethod
    def write_private(path: Path, content: bytes) -> None:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
            temp_path = Path(file.name)
            try:
                file.write(content)
                file.close()
                os.replace(temp_path, path)
            finally:
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def contact_identity(invite: str) -> tuple[str, ...] | None:
        parts = invite.split("&")
        tokens = [part for part in parts if part.startswith("s=")]
        if len(tokens) != 1 or not tokens[0][2:]:
            return None
        return tuple(part for part in parts if not part.startswith("s="))

    def __init__(
        self, httprint_host: str, accounts_dir: str,
        relays: tuple[str, ...] = (),
        bot_name: str = "HTTPrint", bot_icon: str = "",
    ):
        self.httprint = Httprint(httprint_host)
        self.accounts_dir = Path(accounts_dir)
        self.relays = self.parse_relays(",".join(relays))
        self.bot_name = bot_name.strip()
        if not self.bot_name:
            raise ValueError("BOT_NAME must not be empty")
        self.bot_icon = bot_icon.strip()

    def start(self) -> None:
        self.accounts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.accounts_dir.chmod(0o700)
        rpc_dir = self.accounts_dir / "accounts"
        rpc_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with Rpc(accounts_dir=str(rpc_dir)) as rpc:
            deltachat = DeltaChat(rpc)
            accounts = deltachat.get_all_accounts()
            if len(accounts) > 1:
                raise RuntimeError("Expected at most one Delta Chat account")
            account = accounts[0] if accounts else deltachat.add_account()
            account.set_config("bot", "1")
            self.configure_relays(account)
            self.configure_profile(account)
            self.create_contact_qr(account, rpc, force=not accounts)

            pending_replies = self.cleanup_sent_messages(account)
            account.start_io()
            logger.info("Delta Chat bot is running")
            while True:
                event = account.wait_for_event()
                if event.kind == EventType.INCOMING_MSG:
                    reply = self.handle_message(account, event.msg_id)
                    if reply is not None:
                        pending_replies.add(reply.id)
                elif event.kind == EventType.MSG_DELIVERED and event.msg_id in pending_replies:
                    message = account.get_message_by_id(event.msg_id)
                    self.delete_message(account, message, event.msg_id)
                    pending_replies.remove(event.msg_id)

    def cleanup_sent_messages(self, account) -> set[int]:
        pending = set()
        for archived in (False, True):
            for chat in account.get_chatlist(archived_only=archived, no_specials=True):
                for message in chat.get_messages():
                    snapshot = message.get_snapshot()
                    if (
                        snapshot.from_id != SpecialContactId.SELF
                        or snapshot.is_info
                        or not snapshot.is_bot
                        or snapshot.view_type != "Text"
                    ):
                        continue
                    if snapshot.state in (MessageState.OUT_DELIVERED, MessageState.OUT_MDN_RCVD):
                        self.delete_message(account, message, message.id)
                    elif snapshot.state in (MessageState.OUT_PENDING, MessageState.OUT_FAILED):
                        pending.add(message.id)
        return pending

    def configure_profile(self, account) -> None:
        if account.get_config("displayname") != self.bot_name:
            account.set_config("displayname", self.bot_name)
        data_dir = self.accounts_dir.resolve()
        icon = data_dir / (self.bot_icon or "icon.png")
        if not self.bot_icon and not icon.exists() and not icon.is_symlink():
            return
        icon = icon.resolve(strict=True)
        if not icon.is_relative_to(data_dir):
            raise ValueError("BOT_ICON must be inside the data directory")
        if not icon.is_file():
            raise ValueError("BOT_ICON must point to an image file")
        account.set_avatar(str(icon))

    def configure_relays(self, account) -> None:
        if not self.relays:
            if not account.list_transports():
                account.init_transports()
                if not account.list_transports():
                    raise RuntimeError("Delta Chat did not configure a Chatmail relay")
            return

        state_path = self.accounts_dir / "relay-transports.json"
        mapping = {}
        if state_path.exists():
            mapping = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(mapping, dict) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in mapping.items()
            ):
                raise ValueError("Invalid relay-transports.json")
        transports = account.list_transports()
        addresses = {transport["addr"] for transport in transports}
        mapping = {
            relay: address for relay, address in mapping.items()
            if relay in self.relays and address in addresses
        }
        for relay in self.relays:
            if relay not in mapping:
                matching = [
                    addr for addr in addresses
                    if addr.rsplit("@", 1)[-1].lower() == relay
                ]
                if matching:
                    mapping[relay] = sorted(matching)[0]
                    continue
                invitation = f"DCACCOUNT:https://{relay}/new"
                if transports:
                    account.add_transport_from_qr(invitation)
                else:
                    account.init_transports(invitation)
                transports = account.list_transports()
                new_addresses = {transport["addr"] for transport in transports} - addresses
                if len(new_addresses) != 1:
                    raise RuntimeError(f"Unexpected registration result for Chatmail relay {relay}")
                mapping[relay] = new_addresses.pop()
                addresses = {transport["addr"] for transport in transports}
                self.write_private(state_path, json.dumps(mapping, sort_keys=True).encode())
        keep = set(mapping.values())
        for address in sorted(addresses - keep):
            account.delete_transport(address)
        self.write_private(state_path, json.dumps(mapping, sort_keys=True).encode())

    def create_contact_qr(self, account, rpc, force: bool = False) -> None:
        svg_path = self.accounts_dir / "contact.svg"
        png_path = self.accounts_dir / "contact.png"
        text_path = self.accounts_dir / "contact.txt"
        invite = account.get_qr_code()
        if not invite:
            raise RuntimeError("Delta Chat did not return a contact invitation")
        identity = self.contact_identity(invite)
        if identity is None:
            raise RuntimeError("Delta Chat returned an invalid contact invitation")
        if not force and all(
            path.is_file() and path.stat().st_size for path in (svg_path, png_path, text_path)
        ) and self.contact_identity(text_path.read_text(encoding="utf-8")) == identity:
            return

        svg = rpc.create_qr_svg(invite)
        if not svg:
            raise RuntimeError("Delta Chat did not return a contact QR code")
        png = cairosvg.svg2png(
            bytestring=svg.encode("utf-8"), output_width=2048, output_height=2048
        )
        if not isinstance(png, bytes) or not png:
            raise RuntimeError("Failed to render Delta Chat contact QR as PNG")
        text_path.unlink(missing_ok=True)
        for path, content in (
            (svg_path, svg.encode("utf-8")),
            (png_path, png),
            (text_path, invite.encode("utf-8")),
        ):
            self.write_private(path, content)
        logger.info("Delta Chat contact QR created in %s", self.accounts_dir)

    def handle_message(self, account, msg_id: int):
        message = account.get_message_by_id(msg_id)
        message.mark_seen()
        snapshot = message.get_snapshot()
        if (
            snapshot.from_id == SpecialContactId.SELF
            or snapshot.is_bot
            or snapshot.is_info
        ):
            return

        sender = snapshot.sender.get_snapshot()
        sender_from = sender.address or sender.display_name or f"contact {snapshot.from_id}"
        chat = snapshot.chat
        text = snapshot.text or ""
        if not snapshot.file:
            self.delete_message(account, message, msg_id)
            logger.info("Message received from %s", sender_from)
            return

        filename = snapshot.file_name or Path(snapshot.file).name
        try:
            file_data = open(snapshot.file, "rb")
        except OSError as error:
            self.delete_message(account, message, msg_id)
            logger.info("Message received from %s", sender_from)
            logger.warning("Upload failed: %s", error)
            return chat.send_text(
                "🛑 An error occurred while downloading or uploading the "
                "attachment. Please try again later."
            )

        with file_data:
            self.delete_message(account, message, msg_id)
            logger.info("Message received from %s", sender_from)
            logger.info("Attachment %s received", filename)
            reply = self.httprint.upload(filename, file_data, text)
        sent = chat.send_text(reply)
        logger.info("%s", reply)
        return sent

    @staticmethod
    def delete_message(account, message, msg_id: int) -> None:
        try:
            account.delete_messages([message])
        except JsonRpcError as error:
            logger.error("Failed to delete message %s: %s", msg_id, error)
            raise


if __name__ == "__main__":
    log_level = os.environ.get("LOG_LEVEL", "INFO")

    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(DeltaRpcEventLogFilter())
    logging.getLogger("deltachat_rpc_client").setLevel(logging.WARNING)

    bot = HttprintDeltaChatBot(
        httprint_host=os.environ.get("HTTPRINT_HOST", "http://httprint:7777"),
        accounts_dir=os.environ.get(
            "DELTACHAT_ACCOUNTS_DIR",
            str(Path(__file__).resolve().parent / "data"),
        ),
        relays=HttprintDeltaChatBot.parse_relays(os.environ.get("CHATMAIL_RELAYS", "")),
        bot_name=os.environ.get("BOT_NAME", "HTTPrint"),
        bot_icon=os.environ.get("BOT_ICON", ""),
    )
    bot.start()