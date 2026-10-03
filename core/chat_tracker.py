import os
import sys
import json
import time
import re
import urllib.request
from pathlib import Path
from typing import List, Dict, Any, Optional

from .ide_reloader import get_devtools_port, _eval_ws_expression

INTERRUPTED_SESSIONS_FILE = Path(__file__).resolve().parent.parent / "data" / "interrupted_sessions.json"

class ChatSessionTracker:
    """
    Rastreia e monitora as sessões de chat abertas no Google Antigravity.
    Distingue tarefas ativas (is_busy) de tarefas concluídas (is_idle)
    e orquestra a retomada de sessões interrompidas por Hot-Swap.
    """

    @staticmethod
    def get_open_sessions() -> List[Dict[str, Any]]:
        """
        Retorna a lista de todas as sessões de chat abertas no Antigravity,
        com status em tempo real (BUSY = executando, IDLE = concluído).
        """
        port = get_devtools_port()
        if not port:
            return []

        try:
            url = f"http://127.0.0.1:{port}/json/list"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                targets = json.loads(resp.read().decode())
        except Exception:
            return []

        sessions = []
        js_inspect = """
        (() => {
            const cancelBtn = document.querySelector('button[aria-label*="Cancel"]');
            const sendBtn = document.querySelector('[data-testid="send-button"]');
            const editor = document.querySelector('[contenteditable="true"], [aria-label="Message input"]');
            return {
                href: window.location.href,
                pathname: window.location.pathname,
                title: document.title,
                isBusy: !!cancelBtn,
                isIdle: !cancelBtn && !!editor,
                hasEditor: !!editor
            };
        })()
        """

        for t in targets:
            if t.get("type") == "page":
                tid = t.get("id")
                if not tid:
                    continue
                path = f"/devtools/page/{tid}"
                st = _eval_ws_expression("127.0.0.1", port, path, js_inspect) or {}

                raw_path = st.get("pathname") or t.get("url") or ""
                m = re.search(r'/c/([a-f0-9\-]+)', raw_path)
                conv_id = m.group(1) if m else None

                sessions.append({
                    "target_id": tid,
                    "conv_id": conv_id,
                    "title": st.get("title") or t.get("title", "Antigravity Chat"),
                    "url": st.get("href") or t.get("url", ""),
                    "pathname": st.get("pathname", ""),
                    "is_busy": bool(st.get("isBusy")),
                    "is_idle": bool(st.get("isIdle")),
                    "has_editor": bool(st.get("hasEditor")),
                    "status_label": "Executando" if st.get("isBusy") else ("Concluído" if st.get("isIdle") else "Inativo"),
                    "last_checked": time.time()
                })

        return sessions

    @staticmethod
    def snapshot_busy_sessions() -> List[Dict[str, Any]]:
        """
        Registra as sessões que estão ocupadas executando antes de uma troca de conta.
        Apenas as conversas que forem salvas aqui receberão 'continue' pós-hotswap.
        """
        sessions = ChatSessionTracker.get_open_sessions()
        busy = [s for s in sessions if s.get("is_busy") and s.get("conv_id")]

        try:
            INTERRUPTED_SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(INTERRUPTED_SESSIONS_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "timestamp": time.time(),
                    "sessions": busy
                }, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

        return busy

    @staticmethod
    def get_interrupted_sessions() -> List[Dict[str, Any]]:
        """Retorna a lista de sessões salvas que foram interrompidas."""
        if not INTERRUPTED_SESSIONS_FILE.exists():
            return []
        try:
            with open(INTERRUPTED_SESSIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("sessions", [])
        except Exception:
            return []

    @staticmethod
    def clear_interrupted_sessions(conv_id: Optional[str] = None):
        """Remove a sessão da lista de pendências após o continue bem-sucedido."""
        if not INTERRUPTED_SESSIONS_FILE.exists():
            return
        if not conv_id:
            try:
                INTERRUPTED_SESSIONS_FILE.unlink(missing_ok=True)
            except Exception:
                pass
            return

        try:
            with open(INTERRUPTED_SESSIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            sessions = [s for s in data.get("sessions", []) if s.get("conv_id") != conv_id]
            if not sessions:
                INTERRUPTED_SESSIONS_FILE.unlink(missing_ok=True)
            else:
                data["sessions"] = sessions
                with open(INTERRUPTED_SESSIONS_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
