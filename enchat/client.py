import asyncio
import json
import websockets
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

    def __init__(self):
        super().__init__()
        self.config = EnChatConfig()
        self.user_data = self.config.load_config()
        self.user_id = self.user_data["user_id"]
        self.relay_url = self.user_data["relay_url"]
        self.active_chat = None
        self.contacts_keys = {}  # Map ID -> Public Key PEM bytes
        self.websocket = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main-container"):
            with Vertical(id="sidebar"):
                yield Label(f" [ID: {self.user_id}]", classes="chat-item")
                yield Static("--- Chats ---", classes="chat-item")
                yield ListView(id="chat-list")
            with Vertical(id="chat-pane"):
                yield RichLog(id="message-log", highlight=True, markup=True)
        yield Input(placeholder="Type message or command (/add, /group, /chats, /relay, /nick, /help, /exit)...", id="input-bar")
        yield Footer()

    async def on_mount(self):
        self.title = f"EnChat - Secure E2EE Terminal"
        self.sub_title = f"User ID: {self.user_id}"
        self.load_chats_to_sidebar()
        self.run_worker(self.connect_to_relay())

    async def connect_to_relay(self):
        while True:
            try:
                async with websockets.connect(self.relay_url) as websocket:
                    self.websocket = websocket

                    # Register identity and public key
                    await websocket.send(json.dumps({
                        "type": "register",
                        "user_id": self.user_id,
                        "public_key": self.config.get_public_bytes().decode("utf-8")
                    }))

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
                                log.write(f"[green]Successfully retrieved public key for {target_display}. You can now chat![/]")

                        elif msg_type == "message":
                            sender = data.get("from")
                            payload = data.get("payload")

                            # Decrypt message using our private key
                            try:
                                decrypted = self.config.decrypt_message(payload)
                            except Exception:
                                decrypted = "[Encrypted payload decryption failed]"

                            self.config.append_chat_log(sender, sender, decrypted)
                            if self.active_chat == sender:
                                log = self.query_one("#message-log", RichLog)
                                sender_display = self.config.get_nickname(sender)
                                log.write(f"[bold cyan]{sender_display}[/]: {decrypted}")
            except Exception as e:
                log = self.query_one("#message-log", RichLog)
                log.write(f"[italic red]Connection lost. Retrying in 5s... ({e})[/]")
                await asyncio.sleep(5)

    def load_chats_to_sidebar(self):
        chat_list = self.query_one("#chat-list", ListView)
        chat_list.clear()
        chat_files = list(self.config.CHATS_DIR.glob("*.json"))
        for file in chat_files:
            chat_id = file.stem
            nickname = self.config.get_nickname(chat_id)

            if nickname and nickname != chat_id:
                display_text = f"{nickname} ({chat_id})"
            else:
                display_text = chat_id

            chat_list.append(ListItem(Label(f"💬 {display_text}"), name=chat_id))

    async def on_input_submitted(self, event: Input.Submitted):
        text = event.value.strip()
        event.input.value = ""

        if not text:
            return

        if text.startswith("/"):
            await self.handle_command(text)
        else:
            if not self.active_chat:
                log = self.query_one("#message-log", RichLog)
                log.write("[bold red]Error: No active chat selected. Use /add <ID> first.[/]")
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
            log.write("[cyan]/group <NAME>[/]: Create or join a local group chat.")
            log.write("[cyan]/relay <local|cloud|url>[/]: Switch between local server, cloud relay, or custom URL on the fly.")
            log.write("[cyan]/export [filename][/]: Export your account and keys to a backup file.")
            log.write("[cyan]/import <filename>[/]: Import an account backup file.")
            log.write("[cyan]/help[/]: Display this help menu.")
            log.write("[cyan]/exit[/]: Quit EnChat.")
        elif cmd == "/add" and len(parts) > 1:
            target_id = parts[1].upper()
            self.active_chat = target_id
            target_display = self.config.get_nickname(target_id)
            log.write(f"[green]Switched active chat to user: {target_display}[/]")

            if self.websocket:
                await self.websocket.send(json.dumps({
                    "type": "get_pubkey",
                    "target_id": target_id
                }))

            history = self.config.get_chat_history(target_id)
            log.clear()
            log.write(f"[bold blue]--- Chat History with {target_display} ---[/]")
            for h in history:
                sender_display = "You" if h['sender'] == "You" else self.config.get_nickname(h['sender'])
                log.write(f"[cyan]{sender_display}[/]: {h['message']}")
        elif cmd == "/group" and len(parts) > 2:
            group_name = parts[1]
            self.active_chat = group_name
            log.write(f"[green]Created/Joined local group: {group_name}[/]")
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

    async def on_list_view_selected(self, event: ListView.Selected):
        if event.item and event.item.name:
            self.active_chat = event.item.name
            log = self.query_one("#message-log", RichLog)
            log.clear()
            target_display = self.config.get_nickname(self.active_chat)
            log.write(f"[bold blue]--- Chat History with {target_display} ---[/]")
            history = self.config.get_chat_history(self.active_chat)
            for h in history:
                sender_display = "You" if h['sender'] == "You" else self.config.get_nickname(h['sender'])
                log.write(f"[cyan]{sender_display}[/]: {h['message']}")
