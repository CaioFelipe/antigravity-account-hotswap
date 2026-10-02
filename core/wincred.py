import ctypes
from ctypes import wintypes
from typing import Optional, Dict

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2

class CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]

PCREDENTIAL = ctypes.POINTER(CREDENTIAL)
advapi32 = ctypes.windll.advapi32

CredReadW = advapi32.CredReadW
CredReadW.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(PCREDENTIAL)]
CredReadW.restype = wintypes.BOOL

CredWriteW = advapi32.CredWriteW
CredWriteW.argtypes = [PCREDENTIAL, wintypes.DWORD]
CredWriteW.restype = wintypes.BOOL

CredFree = advapi32.CredFree
CredFree.argtypes = [ctypes.c_void_p]

CredDeleteW = advapi32.CredDeleteW
CredDeleteW.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
CredDeleteW.restype = wintypes.BOOL


def read_credential(target: str = "gemini:antigravity") -> Optional[Dict[str, str]]:
    """Lê a credencial especificada do Windows Credential Manager."""
    pcred = PCREDENTIAL()
    if CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        try:
            cred = pcred.contents
            blob = bytes(cred.CredentialBlob[:cred.CredentialBlobSize])
            return {
                "username": cred.UserName or "antigravity",
                "blob": blob.decode("utf-8", errors="replace")
            }
        finally:
            CredFree(pcred)
    return None


def write_credential(target: str = "gemini:antigravity", username: str = "antigravity", blob_str: str = "") -> bool:
    """Grava ou atualiza a credencial no Windows Credential Manager."""
    blob_bytes = blob_str.encode("utf-8")
    blob_array = (ctypes.c_byte * len(blob_bytes))(*blob_bytes)

    cred = CREDENTIAL()
    cred.Flags = 0
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = target
    cred.Comment = None
    cred.CredentialBlobSize = len(blob_bytes)
    cred.CredentialBlob = ctypes.cast(blob_array, ctypes.POINTER(ctypes.c_byte))
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.AttributeCount = 0
    cred.Attributes = None
    cred.TargetAlias = None
    cred.UserName = username

    return bool(CredWriteW(ctypes.byref(cred), 0))


def delete_credential(target: str = "gemini:antigravity") -> bool:
    """Remove a credencial do Windows Credential Manager."""
    return bool(CredDeleteW(target, CRED_TYPE_GENERIC, 0))
