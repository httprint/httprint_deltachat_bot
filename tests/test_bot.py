import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import httpx
from deltachat_rpc_client import DeltaChat, EventType, JsonRpcError, Rpc
from deltachat_rpc_client.const import MessageState

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from app import HttprintDeltaChatBot
from httprint import Httprint

TEST_RELAYS = ("chatmail.bytea.org", "nine.testrun.org")


class FakeAccount:
    def __init__(self, domains=()):
        self.transports = [{"addr": f"bot@{domain}"} for domain in domains]
        self.registered = []

    def list_transports(self):
        return list(self.transports)

    def init_transports(self, invitation=None):
        if invitation is None:
            self.registered.append("automatic")
            self.transports.append({"addr": "bot@automatic.example"})
        else:
            self.add_transport_from_qr(invitation)

    def add_transport_from_qr(self, invitation):
        domain = invitation.removeprefix("DCACCOUNT:https://").removesuffix("/new")
        self.registered.append(domain)
        self.transports.append({"addr": f"bot@{domain}"})

    def delete_transport(self, address):
        self.transports = [item for item in self.transports if item["addr"] != address]


class BotTests(unittest.TestCase):
    def test_housekeeping_is_requested_every_ten_minutes(self):
        account = Mock()
        deltachat = Mock()
        stop = Mock()
        stop.wait.side_effect = [False, False, True]
        actions = Mock()
        actions.attach_mock(account.set_config, "set_config")
        actions.attach_mock(deltachat.maybe_network, "maybe_network")

        HttprintDeltaChatBot.request_periodic_housekeeping(account, deltachat, stop)

        self.assertEqual(stop.wait.call_args_list, [call(600), call(600), call(600)])
        self.assertEqual(actions.mock_calls, [
            call.set_config("last_housekeeping", None), call.maybe_network(),
            call.set_config("last_housekeeping", None), call.maybe_network(),
        ])

    def test_housekeeping_rpc_failures_are_reported_and_retried(self):
        for failing_method in ("set_config", "maybe_network"):
            with self.subTest(failing_method=failing_method):
                account = Mock()
                deltachat = Mock()
                stop = Mock()
                stop.wait.side_effect = [False, False, True]
                method = account.set_config if failing_method == "set_config" else deltachat.maybe_network
                method.side_effect = [JsonRpcError("unavailable"), None]
                with self.assertLogs("app", level="ERROR") as logs:
                    HttprintDeltaChatBot.request_periodic_housekeeping(account, deltachat, stop)
                self.assertIn("Failed to request Delta Chat housekeeping", logs.output[0])
                self.assertEqual(method.call_count, 2)
                self.assertEqual(deltachat.maybe_network.call_count,
                                 1 if failing_method == "set_config" else 2)

    def test_relays_are_registered_once_and_partial_setup_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = HttprintDeltaChatBot("http://example.test", directory, TEST_RELAYS)
            account = FakeAccount()
            bot.configure_relays(account)
            bot.configure_relays(account)
            self.assertEqual(account.registered, list(TEST_RELAYS))
        with tempfile.TemporaryDirectory() as directory:
            bot = HttprintDeltaChatBot("http://example.test", directory, TEST_RELAYS)
            partial = FakeAccount(TEST_RELAYS[:1])
            bot.configure_relays(partial)
            self.assertEqual(partial.registered, [TEST_RELAYS[1]])

    def test_automatic_relay_selected_only_for_new_account(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = HttprintDeltaChatBot("http://example.test", directory)
            account = FakeAccount()
            bot.configure_relays(account)
            bot.configure_relays(account)
            self.assertEqual(account.registered, ["automatic"])
            self.assertEqual(account.transports, [{"addr": "bot@automatic.example"}])
            existing = FakeAccount(["existing.example"])
            bot.configure_relays(existing)
            self.assertEqual(existing.registered, [])
            self.assertEqual(existing.transports, [{"addr": "bot@existing.example"}])

    def test_automatic_relay_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = HttprintDeltaChatBot("http://example.test", directory)
            account = FakeAccount()
            account.init_transports = Mock()
            with self.assertRaises(RuntimeError):
                bot.configure_relays(account)

    def test_configured_relays_replace_old_only_after_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = HttprintDeltaChatBot("http://example.test", directory, ("new.example",))
            account = FakeAccount(["old.example"])
            def register(invitation):
                self.assertEqual(account.transports, [{"addr": "bot@old.example"}])
                account.transports.append({"addr": "bot@mail.other.example"})
            account.add_transport_from_qr = Mock(side_effect=register)
            bot.configure_relays(account)
            self.assertEqual(account.transports, [{"addr": "bot@mail.other.example"}])
            bot.configure_relays(account)
            account.add_transport_from_qr.assert_called_once()

    def test_failed_registration_preserves_existing_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = HttprintDeltaChatBot("http://example.test", directory, ("new.example",))
            account = FakeAccount(["old.example"])
            account.add_transport_from_qr = Mock(side_effect=RuntimeError("unavailable"))
            with self.assertRaises(RuntimeError):
                bot.configure_relays(account)
            self.assertEqual(account.transports, [{"addr": "bot@old.example"}])

    def test_relay_list_validation(self):
        self.assertEqual(HttprintDeltaChatBot.parse_relays("Example.org, example.org, other.example"),
                         ("example.org", "other.example"))
        self.assertEqual(HttprintDeltaChatBot.parse_relays(""), ())
        self.assertEqual(HttprintDeltaChatBot.parse_relays("   "), ())
        for value in ("example.org,", "https://example.org", "example.org/new"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                HttprintDeltaChatBot.parse_relays(value)

    def test_profile_name_and_optional_icon(self):
        with tempfile.TemporaryDirectory() as directory:
            icon = Path(directory) / "avatar.png"
            icon.write_bytes(b"avatar")
            bot = HttprintDeltaChatBot(
                "http://example.test", directory, bot_name="Print bot", bot_icon="avatar.png"
            )
            account = Mock()
            account.get_config.return_value = "Old name"
            account.get_avatar.return_value = None
            bot.configure_profile(account)
            account.set_config.assert_called_once_with("displayname", "Print bot")
            account.set_avatar.assert_called_once_with(str(icon))
            account.get_avatar.return_value = str(icon)
            account.set_avatar.reset_mock()
            bot.configure_profile(account)
            account.set_avatar.assert_called_once_with(str(icon))
            icon.unlink()
            with self.assertRaises(FileNotFoundError):
                bot.configure_profile(account)

    def test_default_icon_and_explicit_icon_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            default_icon = root / "icon.png"
            explicit_icon = root / "other.png"
            account = Mock()
            account.get_config.return_value = "HTTPrint"
            bot = HttprintDeltaChatBot("http://example.test", directory)
            bot.configure_profile(account)
            account.set_avatar.assert_not_called()
            default_icon.write_bytes(b"first")
            bot.configure_profile(account)
            account.set_avatar.assert_called_once_with(str(default_icon))
            default_icon.write_bytes(b"second")
            bot.configure_profile(account)
            self.assertEqual(account.set_avatar.call_count, 2)
            explicit_icon.write_bytes(b"other")
            bot = HttprintDeltaChatBot(
                "http://example.test", directory, bot_icon=str(explicit_icon)
            )
            account.set_avatar.reset_mock()
            bot.configure_profile(account)
            account.set_avatar.assert_called_once_with(str(explicit_icon))

    def test_icon_path_must_stay_inside_data_directory(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "data"
            root.mkdir()
            outside = Path(parent) / "outside.png"
            outside.write_bytes(b"image")
            (root / "link.png").symlink_to(outside)
            (root / "icon.png").symlink_to(outside)
            account = Mock()
            account.get_config.return_value = "HTTPrint"
            for icon in ("../outside.png", str(outside), "link.png", ""):
                with self.subTest(icon=icon):
                    bot = HttprintDeltaChatBot(
                        "http://example.test", str(root), bot_icon=icon
                    )
                    with self.assertRaisesRegex(ValueError, "inside the data directory"):
                        bot.configure_profile(account)
            account.set_avatar.assert_not_called()

    def test_icon_path_accepts_nested_files_inside_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "images"
            nested.mkdir()
            icon = nested / "avatar.png"
            icon.write_bytes(b"image")
            account = Mock()
            account.get_config.return_value = "HTTPrint"
            bot = HttprintDeltaChatBot(
                "http://example.test", directory, bot_icon="images/avatar.png"
            )
            bot.configure_profile(account)
            account.set_avatar.assert_called_once_with(str(icon))

    def test_account_directory_is_always_accounts_subdirectory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "icon.png").write_bytes(b"icon")
            account = Mock()
            account.list_transports.return_value = [{"addr": "bot@example.org"}]
            account.get_chatlist.return_value = []
            account.wait_for_event.side_effect = RuntimeError("stop")
            manager = Mock()
            manager.get_all_accounts.return_value = [account]
            bot = HttprintDeltaChatBot("http://example.test", directory)
            bot.create_contact_qr = Mock()
            with patch("app.Rpc") as rpc, patch("app.DeltaChat", return_value=manager):
                with self.assertRaisesRegex(RuntimeError, "stop"):
                    bot.start()
            rpc.assert_called_once_with(accounts_dir=str(root / "accounts"))
            account.set_avatar.assert_called_once_with(str(root / "icon.png"))

    def test_qr_files_are_private_and_recreated_when_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = HttprintDeltaChatBot("http://example.test", directory)
            account = Mock()
            account.get_qr_code.return_value = "https://i.delta.chat/#key&i=id&s=token&a=one&n=bot"
            account.list_transports.return_value = [{"addr": "bot@example.org"}]
            account.get_config.return_value = "bot"
            rpc = Mock()
            rpc.create_qr_svg.return_value = (
                '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
                '<rect width="10" height="10"/></svg>'
            )
            bot.create_contact_qr(account, rpc)
            svg = Path(directory) / "contact.svg"
            png = Path(directory) / "contact.png"
            text = Path(directory) / "contact.txt"
            self.assertEqual(text.read_text(encoding="utf-8"), account.get_qr_code.return_value)
            rpc.create_qr_svg.assert_called_once_with(account.get_qr_code.return_value)
            self.assertTrue(png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(struct.unpack(">II", png.read_bytes()[16:24]), (2048, 2048))
            self.assertEqual(svg.stat().st_mode & 0o777, 0o600)
            self.assertEqual(png.stat().st_mode & 0o777, 0o600)
            self.assertEqual(text.stat().st_mode & 0o777, 0o600)
            account.get_qr_code.return_value = "https://i.delta.chat/#key&i=id&s=another&a=one&n=bot"
            bot.create_contact_qr(account, rpc)
            self.assertEqual(rpc.create_qr_svg.call_count, 1)
            png.unlink()
            bot.create_contact_qr(account, rpc)
            self.assertEqual(rpc.create_qr_svg.call_count, 2)
            bot.create_contact_qr(account, rpc, force=True)
            self.assertEqual(rpc.create_qr_svg.call_count, 3)
            account.get_qr_code.return_value = "https://i.delta.chat/#key&i=id&s=third&a=other&n=bot"
            bot.create_contact_qr(account, rpc)
            self.assertEqual(rpc.create_qr_svg.call_count, 4)
            account.get_qr_code.return_value = "https://i.delta.chat/#key&i=id&s=fourth&a=other&n=new"
            bot.create_contact_qr(account, rpc)
            self.assertEqual(rpc.create_qr_svg.call_count, 5)
            text.unlink()
            account.get_qr_code.return_value = "https://i.delta.chat/#key&i=id&s=fifth&a=other&n=new"
            bot.create_contact_qr(account, rpc)
            self.assertEqual(text.read_text(encoding="utf-8"), account.get_qr_code.return_value)
            rpc.create_qr_svg.assert_called_with(account.get_qr_code.return_value)
            self.assertEqual(rpc.create_qr_svg.call_count, 6)

    def test_incomplete_invite_update_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = HttprintDeltaChatBot("http://example.test", directory)
            account = Mock()
            account.get_qr_code.return_value = "https://i.delta.chat/#key&i=id&s=token&a=one&n=bot"
            account.list_transports.return_value = [{"addr": "bot@example.org"}]
            account.get_config.return_value = "bot"
            rpc = Mock()
            rpc.create_qr_svg.return_value = (
                '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
            )
            bot.create_contact_qr(account, rpc)
            write = bot.write_private

            def failing_write(path, content):
                if path.name == "contact.txt":
                    raise OSError("write failed")
                write(path, content)

            with patch.object(bot, "write_private", side_effect=failing_write):
                with self.assertRaisesRegex(OSError, "write failed"):
                    bot.create_contact_qr(account, rpc, force=True)
            self.assertFalse((Path(directory) / "contact.txt").exists())
            bot.create_contact_qr(account, rpc)
            self.assertEqual(rpc.create_qr_svg.call_count, 3)

    def test_invalid_contact_invitation_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = HttprintDeltaChatBot("http://example.test", directory)
            account = Mock()
            account.get_qr_code.return_value = "https://i.delta.chat/#key&i=id"
            rpc = Mock()
            with self.assertRaisesRegex(RuntimeError, "invalid contact invitation"):
                bot.create_contact_qr(account, rpc)
            rpc.create_qr_svg.assert_not_called()

    def test_real_delta_chat_invitation_tokens_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
            with patch.dict(os.environ, {"PATH": path}), Rpc(accounts_dir=directory) as rpc:
                account = DeltaChat(rpc).add_account()
                account.add_pseudo_transport("bot@example.org")
                account.set_config("configured_addr", "bot@example.org")
                first = account.get_qr_code()
                second = account.get_qr_code()
                self.assertNotEqual(first, second)
                self.assertIsNotNone(HttprintDeltaChatBot.contact_identity(first))
                self.assertEqual(
                    HttprintDeltaChatBot.contact_identity(first),
                    HttprintDeltaChatBot.contact_identity(second),
                )
            rpc.process.wait(timeout=10)
            rpc.process.stdout.close()
            rpc.process.stderr.close()

    def test_message_deleted_before_upload_with_open_attachment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file.pdf"
            path.write_bytes(b"file")
            chat = Mock()
            sender = SimpleNamespace(
                get_snapshot=lambda: SimpleNamespace(address="test@example.test", display_name="")
            )
            snapshot = SimpleNamespace(
                from_id=99, is_bot=False, is_info=False, sender=sender, chat=chat,
                text="a4", file=str(path), file_name="original.pdf",
            )
            message = Mock()
            message.get_snapshot.return_value = snapshot
            account = Mock()
            account.get_message_by_id.return_value = message
            account.delete_messages.side_effect = lambda _: path.unlink()
            bot = HttprintDeltaChatBot("http://example.test", directory)
            bot.httprint.upload = Mock(side_effect=lambda _, file, __: file.read().decode())
            bot.handle_message(account, 1)
            account.delete_messages.assert_called_once_with([message])
            self.assertEqual(bot.httprint.upload.call_args.args[0], "original.pdf")
            self.assertEqual(bot.httprint.upload.call_args.args[2], "a4")
            chat.send_text.assert_called_once_with("file")
            self.assertFalse(path.exists())

    def test_attachment_uses_blob_name_when_filename_is_missing(self):
        for filename in (None, ""):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "blob.pdf"
                path.write_bytes(b"file")
                sender = SimpleNamespace(
                    get_snapshot=lambda: SimpleNamespace(
                        address="test@example.test", display_name=""
                    )
                )
                message = Mock()
                message.get_snapshot.return_value = SimpleNamespace(
                    from_id=99, is_bot=False, is_info=False, sender=sender, chat=Mock(),
                    text="", file=str(path), file_name=filename
                )
                account = Mock()
                account.get_message_by_id.return_value = message
                bot = HttprintDeltaChatBot("http://example.test", directory)
                bot.httprint.upload = Mock(return_value="done")
                bot.handle_message(account, 1)
                self.assertEqual(bot.httprint.upload.call_args.args[0], "blob.pdf")

    def test_sent_history_cleanup_preserves_pending_and_incoming(self):
        def message(msg_id, from_id, state, is_info=False, is_bot=True, view_type="Text"):
            result = Mock(id=msg_id)
            result.get_snapshot.return_value = SimpleNamespace(
                from_id=from_id, state=state, is_info=is_info,
                is_bot=is_bot, view_type=view_type
            )
            return result

        delivered = message(10, 1, MessageState.OUT_DELIVERED)
        read = message(11, 1, MessageState.OUT_MDN_RCVD)
        pending = message(12, 1, MessageState.OUT_PENDING)
        failed = message(13, 1, MessageState.OUT_FAILED)
        incoming = message(14, 99, MessageState.IN_SEEN)
        info = message(15, 1, MessageState.OUT_DELIVERED, is_info=True)
        non_bot = message(16, 1, MessageState.OUT_DELIVERED, is_bot=False)
        file_message = message(17, 1, MessageState.OUT_DELIVERED, view_type="File")
        normal_chat = Mock()
        normal_chat.get_messages.return_value = [
            delivered, pending, incoming, info, non_bot, file_message
        ]
        archived_chat = Mock()
        archived_chat.get_messages.return_value = [read, failed]
        account = Mock()
        account.get_chatlist.side_effect = [[normal_chat], [archived_chat]]
        bot = HttprintDeltaChatBot("http://example.test", "/tmp")

        self.assertEqual(bot.cleanup_sent_messages(account), {12, 13})
        self.assertEqual(account.delete_messages.call_args_list, [
            call([delivered]), call([read])
        ])

    def test_sent_history_cleanup_with_real_delta_chat(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
            with patch.dict(os.environ, {"PATH": path}), Rpc(accounts_dir=directory) as rpc:
                account = DeltaChat(rpc).add_account()
                account.add_pseudo_transport("bot@example.org")
                account.set_config("configured_addr", "bot@example.org")
                account.set_config("bot", "1")
                chat = account.self_contact.create_chat()
                message = chat.send_text("test reply")
                self.assertEqual(message.get_snapshot().state, MessageState.OUT_DELIVERED)
                bot = HttprintDeltaChatBot("http://example.test", directory)
                self.assertEqual(bot.cleanup_sent_messages(account), set())
                self.assertEqual(chat.get_messages(), [])
            rpc.process.wait(timeout=10)
            rpc.process.stdout.close()
            rpc.process.stderr.close()

    def test_delivery_events_delete_only_tracked_responses(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = HttprintDeltaChatBot("http://example.test", directory)
            bot.configure_relays = Mock()
            bot.configure_profile = Mock()
            bot.create_contact_qr = Mock()
            bot.cleanup_sent_messages = Mock(return_value={42})
            bot.handle_message = Mock(return_value=SimpleNamespace(id=43))
            account = Mock()
            account.wait_for_event.side_effect = [
                SimpleNamespace(kind=EventType.MSG_DELIVERED, msg_id=99),
                SimpleNamespace(kind=EventType.INCOMING_MSG, msg_id=1),
                SimpleNamespace(kind=EventType.MSG_DELIVERED, msg_id=42),
                SimpleNamespace(kind=EventType.MSG_DELIVERED, msg_id=43),
                RuntimeError("stop"),
            ]
            manager = Mock()
            manager.get_all_accounts.return_value = [account]
            with patch("app.Rpc"), patch("app.DeltaChat", return_value=manager):
                with self.assertRaisesRegex(RuntimeError, "stop"):
                    bot.start()
            self.assertEqual(account.get_message_by_id.call_args_list, [
                call(42), call(43)
            ])
            self.assertEqual(account.delete_messages.call_count, 2)

    def test_deletion_failure_aborts_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file.pdf"
            path.write_bytes(b"file")
            message = Mock()
            message.get_snapshot.return_value = SimpleNamespace(
                from_id=99, is_bot=False, is_info=False,
                sender=SimpleNamespace(get_snapshot=lambda: SimpleNamespace(
                    address="test@example.test", display_name=""
                )), chat=Mock(), text="", file=str(path), file_name="original.pdf"
            )
            account = Mock()
            account.get_message_by_id.return_value = message
            account.delete_messages.side_effect = JsonRpcError("deletion failed")
            bot = HttprintDeltaChatBot("http://example.test", directory)
            bot.httprint.upload = Mock()
            with self.assertLogs("app", level="ERROR"):
                with self.assertRaises(JsonRpcError):
                    bot.handle_message(account, 1)
            bot.httprint.upload.assert_not_called()


class HttprintTests(unittest.TestCase):
    def test_print_options(self):
        self.assertEqual(
            Httprint.parseopt("duplex A4 mono 3"),
            {"sides": "two-sided-long-edge", "media": "A4", "color": "false", "copies": 3},
        )

    def test_response_handling(self):
        for status, body, expected in (
            (200, {"message": "done"}, "✅ done"),
            (200, {"error": True, "message": "bad"}, "❌ bad"),
            (400, {"message": "rejected"}, "❌ rejected"),
        ):
            with self.subTest(status=status, body=body), tempfile.TemporaryFile() as file:
                file.write(b"file")
                file.seek(0)
                response = httpx.Response(
                    status, json=body, request=httpx.Request("POST", "http://example.test")
                )
                with patch("httprint.httpx.post", return_value=response) as post:
                    self.assertEqual(Httprint("http://example.test").upload("file", file, "a4 2"), expected)
                    self.assertEqual(post.call_args.kwargs["params"], {"media": "A4", "copies": 2})
                    self.assertEqual(post.call_args.kwargs["files"]["file"], ("file", file))


if __name__ == "__main__":
    unittest.main()
