from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


class SecretStorageError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("data", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class DpapiTokenStore:
    """Stores the user-owned API token encrypted for the current Windows account."""

    ENVIRONMENT_VARIABLE = "DELTA_LOOT_ORZICE_TOKEN"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> str | None:
        environment_token = os.environ.get(self.ENVIRONMENT_VARIABLE, "").strip()
        if environment_token:
            return environment_token
        if not self.path.exists():
            return None
        encrypted = self.path.read_bytes()
        if not encrypted:
            return None
        return self._unprotect(encrypted).decode("utf-8").strip() or None

    def save(self, token: str) -> None:
        normalized = token.strip()
        if not normalized:
            raise ValueError("Token 不能为空")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = self._protect(normalized.encode("utf-8"))
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary_path.write_bytes(encrypted)
        temporary_path.replace(self.path)

    @staticmethod
    def _blob(payload: bytes) -> tuple[_DataBlob, ctypes.Array]:
        buffer = ctypes.create_string_buffer(payload)
        blob = _DataBlob(
            len(payload),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
        )
        return blob, buffer

    @classmethod
    def _protect(cls, payload: bytes) -> bytes:
        if os.name != "nt":
            raise SecretStorageError("DPAPI Token 存储仅支持 Windows")
        source, source_buffer = cls._blob(payload)
        destination = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        success = crypt32.CryptProtectData(
            ctypes.byref(source),
            "DeltaLootAssistant Orzice token",
            None,
            None,
            None,
            0x01,
            ctypes.byref(destination),
        )
        del source_buffer
        if not success:
            raise SecretStorageError(ctypes.FormatError())
        try:
            return ctypes.string_at(destination.data, destination.size)
        finally:
            kernel32.LocalFree(destination.data)

    @classmethod
    def _unprotect(cls, payload: bytes) -> bytes:
        if os.name != "nt":
            raise SecretStorageError("DPAPI Token 存储仅支持 Windows")
        source, source_buffer = cls._blob(payload)
        destination = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        success = crypt32.CryptUnprotectData(
            ctypes.byref(source),
            None,
            None,
            None,
            None,
            0x01,
            ctypes.byref(destination),
        )
        del source_buffer
        if not success:
            raise SecretStorageError(ctypes.FormatError())
        try:
            return ctypes.string_at(destination.data, destination.size)
        finally:
            kernel32.LocalFree(destination.data)
