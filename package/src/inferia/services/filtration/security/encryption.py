from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os
import base64
import json
from typing import Dict, Union
import logging

logger = logging.getLogger(__name__)

class LogEncryption:
    def __init__(self, key_hex: str):
        """
        Initialize with a hex-encoded 32-byte key.
        """
        if not key_hex:
            raise ValueError("LOG_ENCRYPTION_KEY must be set")
            
        try:
            self.key = bytes.fromhex(key_hex)
            if len(self.key) != 32:
                raise ValueError("Key must be 32 bytes (64 hex chars) for AES-256")
            self.aesgcm = AESGCM(self.key)
        except Exception as e:
            logger.error(f"Failed to initialize encryption: {e}")
            raise e

    def encrypt(self, data: Union[Dict, str]) -> str:
        """
        Encrypts data (dict or string) and returns a base64 encoded string.
        Format: nonce + ciphertext
        """
        if isinstance(data, dict):
            plaintext = json.dumps(data).encode('utf-8')
        elif isinstance(data, str):
            plaintext = data.encode('utf-8')
        else:
            raise ValueError("Data must be dict or string")

        nonce = os.urandom(12)
        ciphertext = self.aesgcm.encrypt(nonce, plaintext, None)
        
        # Combine nonce + ciphertext and base64 encode
        combined = nonce + ciphertext
        return base64.b64encode(combined).decode('utf-8')

    def decrypt(self, token: str) -> Union[Dict, str]:
        """
        Decrypts a base64 encoded token.
        Returns dict if valid JSON, otherwise string.
        """
        try:
            combined = base64.b64decode(token)
            nonce = combined[:12]
            ciphertext = combined[12:]
            
            plaintext = self.aesgcm.decrypt(nonce, ciphertext, None)
            decoded = plaintext.decode('utf-8')
            
            try:
                return json.loads(decoded)
            except json.JSONDecodeError:
                return decoded
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise ValueError("Decryption failed")
