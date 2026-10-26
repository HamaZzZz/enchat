import os
import json
import hashlib
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes

class EnChatConfig:
    def __init__(self):
        base_dir = Path(os.environ.get("ENCHAT_HOME", Path.home() / ".config" / "enchat"))

        self.CONFIG_DIR = base_dir
        self.CHATS_DIR = self.CONFIG_DIR / "chats"
        self.CONFIG_FILE = self.CONFIG_DIR / "config.json"
        self.PRIVATE_KEY_FILE = self.CONFIG_DIR / "private.pem"
        self.PUBLIC_KEY_FILE = self.CONFIG_DIR / "public.pem"

        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.CHATS_DIR.mkdir(parents=True, exist_ok=True)
        self.init_keys()
        self.init_config()

    def export_profile(self, export_path: str = "enchat_backup.json"):
        """Exports the entire profile (private key, public key, config) into a single JSON backup file."""
        backup_data = {
            "user_id": self.get_user_id(),
            "config": self.load_config(),
            "private_pem": self.PRIVATE_KEY_FILE.read_text(encoding="utf-8") if self.PRIVATE_KEY_FILE.exists() else "",
            "public_pem": self.PUBLIC_KEY_FILE.read_text(encoding="utf-8") if self.PUBLIC_KEY_FILE.exists() else ""
        }
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(backup_data, f, indent=4)
        return export_path

    def import_profile(self, import_path: str):
        """Restores a profile from a JSON backup file, overwriting local keys and configuration."""
        if not os.path.exists(import_path):
            raise FileNotFoundError(f"Backup file not found: {import_path}")

        with open(import_path, "r", encoding="utf-8") as f:
            backup_data = json.load(f)

        # Write config
        with open(self.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(backup_data.get("config", {}), f, indent=4)

        # Write keys
        if backup_data.get("private_pem"):
            self.PRIVATE_KEY_FILE.write_text(backup_data["private_pem"], encoding="utf-8")
        if backup_data.get("public_pem"):
            self.PUBLIC_KEY_FILE.write_text(backup_data["public_pem"], encoding="utf-8")

        return True

    def init_keys(self):
        if not self.PRIVATE_KEY_FILE.exists() or not self.PUBLIC_KEY_FILE.exists():
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048
            )
            private_bytes = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
            public_key = private_key.public_key()
            public_bytes = public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            )
            with open(self.PRIVATE_KEY_FILE, "wb") as f:
                f.write(private_bytes)
            with open(self.PUBLIC_KEY_FILE, "wb") as f:
                f.write(public_bytes)

    def init_config(self):
        if not self.CONFIG_FILE.exists():
            with open(self.PUBLIC_KEY_FILE, "rb") as f:
                pub_bytes = f.read()
            user_id = hashlib.sha256(pub_bytes).hexdigest()[:8].upper()
            config_data = {
                "user_id": user_id,
                "relay_url": "ws://localhost:8765",
                "nicknames": {}
            }
            with open(self.CONFIG_FILE, "w") as f:
                json.dump(config_data, f, indent=4)

    def load_config(self):
        with open(self.CONFIG_FILE, "r") as f:
            return json.load(f)

    def set_relay_url(self, url: str):
        data = self.load_config()
        data["relay_url"] = url
        with open(self.CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=4)

    def get_public_bytes(self) -> bytes:
        with open(self.PUBLIC_KEY_FILE, "rb") as f:
            return f.read()

    def get_nickname(self, user_id: str) -> str:
        config_data = self.load_config()
        nicknames = config_data.get("nicknames", {})
        return nicknames.get(user_id.upper(), user_id.upper())

    def set_nickname(self, user_id: str, nickname: str):
        config_data = self.load_config()
        if "nicknames" not in config_data:
            config_data["nicknames"] = {}
        config_data["nicknames"][user_id.upper()] = nickname
        with open(self.CONFIG_FILE, "w") as f:
            json.dump(config_data, f, indent=4)

    def encrypt_message(self, recipient_pub_bytes: bytes, message: str) -> str:
        pub_key = serialization.load_pem_public_key(recipient_pub_bytes)
        ciphertext = pub_key.encrypt(
            message.encode("utf-8"),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        return ciphertext.hex()

    def decrypt_message(self, hex_ciphertext: str) -> str:
        with open(self.PRIVATE_KEY_FILE, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
        ciphertext = bytes.fromhex(hex_ciphertext)
        plaintext = private_key.decrypt(
            ciphertext,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        return plaintext.decode("utf-8")

    def get_chat_history(self, peer_id: str):
        chat_file = self.CHATS_DIR / f"{peer_id.upper()}.json"
        if not chat_file.exists():
            return []
        with open(chat_file, "r") as f:
            return json.load(f)

    def append_chat_log(self, peer_id: str, sender: str, message: str):
        chat_file = self.CHATS_DIR / f"{peer_id.upper()}.json"
        history = self.get_chat_history(peer_id)
        history.append({"sender": sender, "message": message})
        with open(chat_file, "w") as f:
            json.dump(history, f, indent=4)
