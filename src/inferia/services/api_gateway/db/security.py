import os
import json
from cryptography.fernet import Fernet
from typing import Any, Dict, Optional
from sqlalchemy.types import TypeDecorator, Text, JSON
from inferia.services.api_gateway.config import settings

# The encryption key should be a 32-byte base64 encoded string
# Users can generate one using: Fernet.generate_key().decode()
ENCRYPTION_KEY = settings.secret_encryption_key or os.getenv("SECRET_ENCRYPTION_KEY")


class EncryptionService:
    def __init__(self):
        self.fernet = None
        if ENCRYPTION_KEY:
            try:
                self.fernet = Fernet(ENCRYPTION_KEY.encode())
            except Exception as e:
                print(f"Error initializing encryption: {e}")

    def encrypt_string(self, text: str) -> str:
        if not self.fernet or not text:
            return text
        return self.fernet.encrypt(text.encode()).decode()

    def decrypt_string(self, encrypted_text: str) -> str:
        if not self.fernet or not encrypted_text:
            return encrypted_text
        try:
            return self.fernet.decrypt(encrypted_text.encode()).decode()
        except Exception:
            # If decryption fails, it might be plain text (for backward compat or if key changed)
            return encrypted_text

    def encrypt_json(self, data: Any) -> Optional[str]:
        """Encrypts a dictionary or list into an encrypted string."""
        if data is None:
            return None
        json_str = json.dumps(data)
        return self.encrypt_string(json_str)

    def decrypt_json(self, encrypted_str: str) -> Any:
        """Decrypts a string back into its original type."""
        if not encrypted_str:
            return None

        # Check if it looks like JSON (unencrypted)
        # This is for backward compatibility with existing data
        stripped = encrypted_str.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(encrypted_str)
            except json.JSONDecodeError:
                # Not valid JSON, proceed to decrypt
                pass

        decrypted = self.decrypt_string(encrypted_str)
        try:
            return json.loads(decrypted)
        except Exception:
            # Fallback if it was just a string
            return decrypted


encryption_service = EncryptionService()


class EncryptedJSON(TypeDecorator):
    """
    SQLAlchemy type that encrypts JSON data before storing it in the database
    and decrypts it when reading.
    Stores as a JSON object: {"data": "encrypted_base64_string"}
    """

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        encrypted_str = encryption_service.encrypt_json(value)
        return {"data": encrypted_str}

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, dict) and "data" in value:
            return encryption_service.decrypt_json(value["data"])

        # Fallback for existing unencrypted JSON data
        return value
