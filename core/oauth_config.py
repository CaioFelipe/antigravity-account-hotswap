import os
import re
import shutil
from pathlib import Path
from typing import Tuple, Optional

_cached_client_id: Optional[str] = None
_cached_client_secret: Optional[str] = None

def get_google_oauth_client() -> Tuple[str, str]:
    """
    Obtém dinamicamente o Client ID e Client Secret oficiais da Google para o Antigravity.
    Prioridades:
    1. Variáveis de ambiente (ANTIGRAVITY_CLIENT_ID / ANTIGRAVITY_CLIENT_SECRET)
    2. Extração segura e automática a partir do binário agy.exe instalado no sistema
    """
    global _cached_client_id, _cached_client_secret
    if _cached_client_id and _cached_client_secret:
        return _cached_client_id, _cached_client_secret

    env_id = os.environ.get("ANTIGRAVITY_CLIENT_ID")
    env_sec = os.environ.get("ANTIGRAVITY_CLIENT_SECRET")
    if env_id and env_sec:
        _cached_client_id = env_id
        _cached_client_secret = env_sec
        return _cached_client_id, _cached_client_secret

    possible_paths = [
        shutil.which("agy"),
        shutil.which("agy.exe"),
        Path(os.path.expanduser(r"~\AppData\Local\agy\bin\agy.exe")),
        Path(os.path.expanduser(r"~\AppData\Local\Programs\antigravity\resources\bin\agy.exe"))
    ]

    for p in possible_paths:
        if p and Path(p).exists():
            try:
                with open(p, "rb") as f:
                    content = f.read()

                ids = re.findall(rb"[0-9]+-[a-z0-9_]+\.apps\.googleusercontent\.com", content)
                secs = re.findall(rb"GOCSPX-[a-zA-Z0-9_\-]+", content)

                target_id = None
                for candidate in ids:
                    dec = candidate.decode("utf-8")
                    if "tmhssin2h21lcre235vtolojh4g403ep" in dec or "1071006060591" in dec:
                        target_id = dec
                        break

                if not target_id and ids:
                    target_id = ids[-1].decode("utf-8")

                target_sec = secs[0].decode("utf-8") if secs else ""

                if target_id and target_sec:
                    _cached_client_id = target_id
                    _cached_client_secret = target_sec
                    return _cached_client_id, _cached_client_secret
            except Exception:
                continue

    return "", ""
