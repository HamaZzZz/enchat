import asyncio
import json
import subprocess
import shutil
import websockets
import os
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Static, Input, ListView, ListItem, Label, RichLog
from .config import EnChatConfig

class EnChatApp(App):
    CSS = """
    Screen {
        background: $surface;
    }
    #main-container {
        layout: horizontal;
        height: 1fr;
    }
    #sidebar {
        width: 30;
        dock: left;
        background: $boost;
        border-right: vkey $primary;
    }
    #chat-pane {
        width: 1fr;
        height: 1fr;
        padding: 1;
    }
    #input-bar {
        dock: bottom;
        height: 3;
        padding: 0 1;
        background: $panel;
    }
    .chat-item {
        padding: 1;
    }
    """

    def format_chat_display(self, chat_id: str) -> str:
        nickname = self.config.get_nickname(chat_id)
        id_display = "(...)" if self.hide_ids else f"({chat_id})"

        if chat_id == "MAIN":
            return "MAIN"

        if nickname and nickname != chat_id:
            clean_nick = nickname.replace("👥 ", "")
            return f"👥 {clean_nick} {id_display}" if "👥" in nickname else f"{nickname} {id_display}"
        else:
            return f"{chat_id}" if self.hide_ids else chat_id

    def __init__(self):
        super().__init__()
        self.config = EnChatConfig()
        self.user_data = self.config.load_config()
        self.user_id = self.user_data["user_id"]
        self.relay_url = self.user_data["relay_url"]
        self.active_chat = None
        self.contacts_keys = {}  # Map ID -> Public Key PEM bytes
        self.websocket = None
        self.joined_groups = set()
        self.temp_histories = {"MAIN": []}
        self.hide_ids = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main-container"):
            with Vertical(id="sidebar"):
                id_text = "[...]" if self.hide_ids else self.user_id
                yield Label(f" [ID: {id_text}]", classes="chat-item", id="user-id-label")
                yield Static("--- Chats ---", classes="chat-item")
                yield ListView(id="chat-list")
            with Vertical(id="chat-pane"):
                yield RichLog(id="message-log", highlight=True, markup=True)
        yield Input(placeholder="Type message or command (/add, /group, /chats, /relay, /nick, /help, /exit)...", id="input-bar")
        yield Footer()

    async def on_mount(self):
        self.title = f"EnChat - Secure E2EE Terminal"
        sub_id_text = "[...]" if self.hide_ids else self.user_id
        self.sub_title = f"User ID: {sub_id_text}"

        # Display splash screen art on startup
        log = self.query_one("#message-log", RichLog)
        ascii_art = (
            "[bold cyan]"
            "  ███████╗███╗   ██╗ ██████╗██╗  ██╗ █████╗ ████████╗\n"
            "  ██╔════╝████╗  ██║██╔════╝██║  ██║██╔══██╗╚══██╔══╝\n"
            "  █████╗  ██╔██╗ ██║██║     ███████║███████║   ██║   \n"
            "  ██╔══╝  ██║╚██╗██║██║     ██╔══██║██╔══██║   ██║   \n"
            "  ███████╗██║ ╚████║╚██████╗██║  ██║██║  ██║   ██║   \n"
            "  ╚══════╝╚═╝  ╚═══╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝"
            "[/]\n\n"
            "[yellow]Welcome to EnChat! Secure, encrypted terminal messaging.[/]\n"
            "[dim]Select a chat from the sidebar or type [/][cyan]/help[/][dim] to see available commands.[/]\n"
            "------------------------------------------------------------------"
        )
        log.write(ascii_art)

        # 1. Restore groups from config nicknames into joined_groups FIRST
        config_data = self.config.load_config()
        nicknames = config_data.get("nicknames", {})
        for chat_id, nick in nicknames.items():
            if "👥" in str(nick):
                self.joined_groups.add(chat_id)

        # 2. Always join MAIN
        self.joined_groups.add("MAIN")

        # 3. Load sidebar UI
        self.load_chats_to_sidebar()

        # 4. NOW start the background relay connection
        self.run_worker(self.connect_to_relay())

    async def connect_to_relay(self):
        while True:
            try:
                async with websockets.connect(self.relay_url) as websocket:
                    self.websocket = websocket
                    self.contacts_keys.clear()

                    # Identity registration
                    await websocket.send(json.dumps({
                        "type": "register",
                        "user_id": self.user_id,
                        "public_key": self.config.get_public_bytes().decode("utf-8")
                    }))

                    for group_id in list(self.joined_groups):
                        await websocket.send(json.dumps({
                            "type": "join_group",
                            "group": group_id,
                            "user_id": self.user_id
                        }))

                    # If we have an active DM peer, fetch their public key immediately
                    if self.active_chat and len(self.active_chat) == 8 and all(c in "0123456789ABCDEFabcdef" for c in self.active_chat):
                        await websocket.send(json.dumps({
                            "type": "get_pubkey",
                            "target_id": self.active_chat
                        }))

                    # Heartbeat keep-alive
                    async def heartbeat():
                        while True:
                            await asyncio.sleep(20)
                            try:
                                await websocket.ping()
                            except Exception:
                                break

                    ping_task = asyncio.create_task(heartbeat())

                    try:
                        async for raw_msg in websocket:
                            data = json.loads(raw_msg)
                            msg_type = data.get("type")

                            if msg_type == "pubkey_response":
                                target_id = data.get("target_id")
                                pub_key_pem = data.get("public_key")
                                if pub_key_pem:
                                    self.contacts_keys[target_id] = pub_key_pem.encode("utf-8")
                                    log = self.query_one("#message-log", RichLog)
                                    target_display = self.config.get_nickname(target_id)
                                    log.write(f"[green]Successfully retrieved public key for {target_display}. You can now send messages![/]")

                            elif msg_type == "message":
                                if shutil.which("paplay"):
                                    sound_path = "/usr/share/sounds/freedesktop/stereo/message.oga"
                                    if not os.path.exists(sound_path):
                                        sound_path = "/usr/share/sounds/freedesktop/stereo/complete.oga"
                                    if os.path.exists(sound_path):
                                        subprocess.Popen(["paplay", sound_path])

                                sender = data.get("from")
                                payload = data.get("payload")

                                try:
                                    decrypted = self.config.decrypt_message(payload)
                                except Exception:
                                    decrypted = "[Message decryption error]"

                                self.config.append_chat_log(sender, sender, decrypted)
                                self.load_chats_to_sidebar()

                                sender_display = self.config.get_nickname(sender)

                                if shutil.which("notify-send"):
                                    subprocess.Popen(["notify-send", "EnChat", f"New message from {sender_display}"])

                                self.notify(f"New message from {sender_display}!", title="EnChat", severity="information", timeout=3)

                                if self.active_chat == sender:
                                    log = self.query_one("#message-log", RichLog)
                                    log.write(f"[bold cyan]{sender_display}[/]: {decrypted}")
                                else:
                                    log = self.query_one("#message-log", RichLog)
                                    log.write(f"[yellow]💬 New message from {sender_display}! Check your chat list.[/]")

                            elif msg_type == "group_message":
                                group_name = data.get("group")
                                sender = data.get("from")
                                payload = data.get("payload")
                                g_name_meta = data.get("group_name")

                                if g_name_meta and g_name_meta != "Group" and g_name_meta != group_name:
                                    self.config.set_nickname(group_name, f"👥 {g_name_meta}")
                                    self.load_chats_to_sidebar()

                                if sender == self.user_id:
                                    continue

                                if group_name == "MAIN":
                                    if "MAIN" not in self.temp_histories:
                                        self.temp_histories["MAIN"] = []
                                    self.temp_histories["MAIN"].append({"sender": sender, "message": payload})

                                    if self.active_chat == "MAIN":
                                        sender_display = self.config.get_nickname(sender)
                                        log = self.query_one("#message-log", RichLog)
                                        log.write(f"[bold cyan]{sender_display}[/]: {payload}")
                                    continue

                                if shutil.which("paplay"):
                                    sound_path = "/usr/share/sounds/freedesktop/stereo/message.oga"
                                    if os.path.exists(sound_path):
                                        subprocess.Popen(["paplay", sound_path])

                                self.config.append_chat_log(group_name, sender, payload)
                                self.load_chats_to_sidebar()

                                sender_display = self.config.get_nickname(sender)

                                if shutil.which("notify-send"):
                                    subprocess.Popen(["notify-send", f"EnChat [{group_name}]", f"{sender_display}: {payload}"])

                                self.notify(f"[{group_name}] {sender_display}: {payload}", title="EnChat Group", severity="information", timeout=3)

                                if self.active_chat == group_name:
                                    log = self.query_one("#message-log", RichLog)
                                    log.write(f"[bold cyan]{sender_display}[/]: {payload}")
                                else:
                                    log = self.query_one("#message-log", RichLog)
                                    log.write(f"[yellow]💬 New message in group [bold]{group_name}[/][/]")
                    finally:
                        ping_task.cancel()

            except Exception as e:
                log = self.query_one("#message-log", RichLog)
                log.write(f"[italic red]Connection lost. Reconnecting in 5s... ({e})[/]")
                await asyncio.sleep(5)

    def load_chats_to_sidebar(self):
        chat_list = self.query_one("#chat-list", ListView)
        chat_list.clear()

        chat_list.append(ListItem(Label("💬 MAIN"), name="MAIN"))

        chat_files = list(self.config.CHATS_DIR.glob("*.json"))
        for file in chat_files:
            chat_id = file.stem
            if chat_id == "MAIN":
                continue

            display_text = self.format_chat_display(chat_id)
            chat_list.append(ListItem(Label(f"💬 {display_text}"), name=chat_id))

    async def on_input_submitted(self, event: Input.Submitted):
        text = event.value.strip()
        event.input.value = ""

        if not text:
            return

        # Check for commands globally first (even if in MAIN or active chat)
        if text.startswith("/"):
            await self.handle_command(text)
            return

        if self.active_chat == "MAIN":
            if self.websocket:
                await self.websocket.send(json.dumps({
                    "type": "group_message",
                    "group": "MAIN",
                    "from": self.user_id,
                    "payload": text
                }))

            if "MAIN" not in self.temp_histories:
                self.temp_histories["MAIN"] = []
            self.temp_histories["MAIN"].append({"sender": "You", "message": text})

            log = self.query_one("#message-log", RichLog)
            log.write(f"[bold green]You[/]: {text}")
            return

        if not self.active_chat:
            log = self.query_one("#message-log", RichLog)
            log.write("[bold red]Error: No active chat selected. Use /add <ID> first.[/]")
            return

        raw_display = self.config.get_nickname(self.active_chat)
        is_group = (
            self.active_chat in self.joined_groups or
            "👥" in raw_display or
            not (len(self.active_chat) == 8 and all(c in "0123456789ABCDEFabcdef" for c in self.active_chat))
        )

        if is_group:
            self.joined_groups.add(self.active_chat)
            raw_display = self.config.get_nickname(self.active_chat)
            clean_group_name = raw_display.replace("👥 ", "") if "👥" in raw_display else (raw_display if raw_display != self.active_chat else "Group")

            if self.websocket:
                await self.websocket.send(json.dumps({
                    "type": "group_message",
                    "group": self.active_chat,
                    "from": self.user_id,
                    "payload": text,
                    "group_name": clean_group_name
                }))
            self.config.append_chat_log(self.active_chat, "You", text)
            self.load_chats_to_sidebar()
            log = self.query_one("#message-log", RichLog)
            log.write(f"[bold green]You[/]: {text}")
            return

        try:
            if self.active_chat not in self.contacts_keys:
                if self.websocket:
                    await self.websocket.send(json.dumps({
                        "type": "get_pubkey",
                        "target_id": self.active_chat
                    }))
                log = self.query_one("#message-log", RichLog)
                target_display = self.config.get_nickname(self.active_chat)
                log.write(f"[yellow]Fetching public key for {target_display}... Please try sending again in a second.[/]")
                return

            recipient_pub_bytes = self.contacts_keys[self.active_chat]
            ciphertext = self.config.encrypt_message(recipient_pub_bytes, text)

            if self.websocket:
                await self.websocket.send(json.dumps({
                    "type": "message",
                    "to": self.active_chat,
                    "from": self.user_id,
                    "payload": ciphertext
                }))

            self.config.append_chat_log(self.active_chat, "You", text)
            log = self.query_one("#message-log", RichLog)
            log.write(f"[bold green]You[/]: {text}")
        except Exception as ex:
            log = self.query_one("#message-log", RichLog)
            log.write(f"[bold red]Failed to send message: {ex}[/]")

    async def handle_command(self, cmd_text: str):
        parts = cmd_text.split(" ")
        cmd = parts[0].lower()
        log = self.query_one("#message-log", RichLog)

        if cmd == "/exit":
            self.exit()
        elif cmd == "/chats":
            self.load_chats_to_sidebar()
            log.write("[yellow]Refreshed chat list in sidebar.[/]")
        elif cmd == "/hideids":
            self.hide_ids = not self.hide_ids
            status = "enabled (hidden)" if self.hide_ids else "disabled (visible)"

            # Update sidebar user ID label
            user_label = self.query_one("#user-id-label", Label)
            id_text = "[...]" if self.hide_ids else self.user_id
            user_label.update(f" [ID: {id_text}]")

            # Update top app sub_title header bar
            sub_id_text = "[...]" if self.hide_ids else self.user_id
            self.sub_title = f"User ID: {sub_id_text}"

            log.write(f"[green]ID hiding is now [bold]{status}[/].[/]")
            self.load_chats_to_sidebar()
        elif cmd == "/export":
            filename = parts[1] if len(parts) > 1 else "enchat_backup.json"
            try:
                path = self.config.export_profile(filename)
                log.write(f"[green]Successfully exported account backup to: [bold]{path}[/][/]")
            except Exception as e:
                log.write(f"[red]Export failed: {e}[/]")
        elif cmd == "/import" and len(parts) > 1:
            filename = parts[1]
            try:
                self.config.import_profile(filename)
                log.write(f"[green]Successfully imported profile from {filename}! Restart EnChat to apply changes.[/]")
            except Exception as e:
                log.write(f"[red]Import failed: {e}[/]")
        elif cmd == "/nick" and len(parts) > 2:
            target_id = parts[1].upper()
            nickname = parts[2]
            self.config.set_nickname(target_id, nickname)
            log.write(f"[green]Nickname for {target_id} set to '[bold]{nickname}[/]'[/]")
            self.load_chats_to_sidebar()
        elif cmd == "/help":
            log.write("[bold yellow]--- EnChat Command Reference ---[/]")
            log.write("[cyan]/add <USER_ID>[/]: Start or switch to a direct chat with a user.")
            log.write("[cyan]/nick <USER_ID> <NAME>[/]: Set a local nickname for a user.")
            log.write("[cyan]/chats[/]: Refresh the chat list in the sidebar.")
            log.write("[cyan]/hideIDs[/]: Toggle visibility of user and group IDs in the sidebar.")
            log.write("[cyan]/group create <NAME>[/]: Create a new secure group with a random ID.")
            log.write("[cyan]/group remove <GROUP_ID>[/]: Remove a group from your sidebar and local history.")
            log.write("[cyan]/group main[/]: Switch to the global temporary MAIN channel.")
            log.write("[cyan]/group <GROUP_ID>[/]: Join an existing secure group using its unique ID.")
            log.write("[cyan]/relay <local|cloud|url>[/]: Switch between local server, cloud relay, or custom URL on the fly.")
            log.write("[cyan]/export [filename][/]: Export your account and keys to a backup file.")
            log.write("[cyan]/import <filename>[/]: Import an account backup file.")
            log.write("[cyan]/help[/]: Display this help menu.")
            log.write("[cyan]/exit[/]: Quit EnChat.")
        elif cmd == "/add" and len(parts) > 1:
            target_id = parts[1].upper()
            self.active_chat = target_id
            target_display = self.config.get_nickname(target_id)
            id_str = "(...)" if self.hide_ids else f"({target_id})"
            log.write(f"[green]Switched active chat to user: {target_display} {id_str}[/]")

            if self.websocket:
                await self.websocket.send(json.dumps({
                    "type": "get_pubkey",
                    "target_id": target_id
                }))

            history = self.config.get_chat_history(target_id)
            log.clear()
            log.write(f"[bold blue]--- Chat History with {target_display} {id_str} ---[/]")
            for h in history:
                sender_display = "You" if h['sender'] == "You" else self.config.get_nickname(h['sender'])
                log.write(f"[cyan]{sender_display}[/]: {h['message']}")
        elif cmd == "/group" and len(parts) > 1:
            sub_action = parts[1].lower()

            if sub_action == "create" and len(parts) > 2:
                group_name = " ".join(parts[2:])
                import secrets
                group_id = secrets.token_hex(4).upper()

                self.config.set_nickname(group_id, f"👥 {group_name}")
                self.active_chat = group_id
                self.joined_groups.add(group_id)

                id_str = "(...)" if self.hide_ids else f"({group_id})"
                log.write(f"[green]Created secure group '[bold]{group_name}[/]' with ID: [bold cyan]{id_str}[/][/]")
                log.write(f"[yellow]Share this ID with friends so they can join using: /group {group_id}[/]")

                if self.websocket:
                    await self.websocket.send(json.dumps({
                        "type": "join_group",
                        "group": group_id,
                        "user_id": self.user_id
                    }))

                history = self.config.get_chat_history(group_id)
                log.clear()
                log.write(f"[bold blue]--- Group: {group_name} {id_str} ---[/]")
                for h in history:
                    sender_display = "You" if h['sender'] == "You" else self.config.get_nickname(h['sender'])
                    log.write(f"[cyan]{sender_display}[/]: {h['message']}")

            elif sub_action == "main":
                self.active_chat = "MAIN"
                self.joined_groups.add("MAIN")

                if self.websocket:
                    await self.websocket.send(json.dumps({
                        "type": "join_group",
                        "group": "MAIN",
                        "user_id": self.user_id
                    }))

                log.clear()
                log.write(f"[bold blue]--- Global Channel: MAIN (Temporary) ---[/]")
                for h in self.temp_histories.get("MAIN", []):
                    sender_display = "You" if h['sender'] == "You" else self.config.get_nickname(h['sender'])
                    log.write(f"[cyan]{sender_display}[/]: {h['message']}")

            elif sub_action == "remove" and len(parts) > 1:
                target_group = parts[2].upper() if len(parts) > 2 else self.active_chat
                if target_group:
                    if target_group in self.joined_groups:
                        self.joined_groups.remove(target_group)

                    chat_file = self.config.CHATS_DIR / f"{target_group}.json"
                    if chat_file.exists():
                        chat_file.unlink()

                    if self.active_chat == target_group:
                        self.active_chat = None
                        log.clear()

                    self.load_chats_to_sidebar()
                    log.write(f"[green]Removed group from your chats.[/]")
            else:
                group_id = parts[1].upper()
                self.active_chat = group_id
                self.joined_groups.add(group_id)

                group_display = self.config.get_nickname(group_id)
                id_str = "(...)" if self.hide_ids else f"({group_id})"
                log.write(f"[green]Joined secure group ID: [bold]{id_str}[/][/]")

                if self.websocket:
                    await self.websocket.send(json.dumps({
                        "type": "join_group",
                        "group": group_id,
                        "user_id": self.user_id
                    }))

                history = self.config.get_chat_history(group_id)
                log.clear()
                log.write(f"[bold blue]--- Group Chat: {group_display} {id_str} ---[/]")
                for h in history:
                    sender_display = "You" if h['sender'] == "You" else self.config.get_nickname(h['sender'])
                    log.write(f"[cyan]{sender_display}[/]: {h['message']}")
        elif cmd == "/relay" and len(parts) > 1:
            target = parts[1].lower()
            if target == "local":
                new_url = "ws://localhost:8765"
            elif target == "cloud":
                new_url = "wss://enchat-relay.onrender.com"
            else:
                new_url = parts[1]

            self.config.set_relay_url(new_url)
            self.relay_url = new_url
            log.write(f"[yellow]Relay URL updated to: {new_url}. Reconnecting...[/]")

            if self.websocket:
                await self.websocket.close()
        else:
            log.write(f"[red]Unknown or invalid command: {cmd_text}[/]")

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if not item or not item.name:
            return

        selected_id = str(item.name).strip()
        self.active_chat = selected_id
        log = self.query_one("#message-log", RichLog)
        log.clear()

        if selected_id == "MAIN":
            log.write("[bold blue]--- Global Channel: MAIN (Temporary) ---[/]")
            for h in self.temp_histories.get("MAIN", []):
                sender_display = "You" if h['sender'] == "You" else self.config.get_nickname(h['sender'])
                log.write(f"[cyan]{sender_display}[/]: {h['message']}")
            return

        raw_display = self.config.get_nickname(selected_id)
        is_group = (
            selected_id in self.joined_groups or
            "👥" in raw_display or
            not (len(selected_id) == 8 and all(c in "0123456789ABCDEFabcdef" for c in selected_id))
        )

        id_str = "(...)" if self.hide_ids else f"({selected_id})"

        if is_group:
            self.joined_groups.add(selected_id)
            group_display = self.config.get_nickname(selected_id)
            log.write(f"[bold blue]--- Group Chat: {group_display} {id_str} ---[/]")
            history = self.config.get_chat_history(selected_id)
            for h in history:
                sender_display = "You" if h['sender'] == "You" else self.config.get_nickname(h['sender'])
                log.write(f"[cyan]{sender_display}[/]: {h['message']}")
            return

        target_display = self.config.get_nickname(selected_id)
        log.write(f"[bold blue]--- Chat History with {target_display} {id_str} ---[/]")
        history = self.config.get_chat_history(selected_id)
        for h in history:
            sender_display = "You" if h['sender'] == "You" else target_display
            log.write(f"[cyan]{sender_display}[/]: {h['message']}")

        if selected_id not in self.contacts_keys and self.websocket:
            await self.websocket.send(json.dumps({
                "type": "get_pubkey",
                "target_id": selected_id
            }))
