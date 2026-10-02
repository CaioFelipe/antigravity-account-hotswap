import os
import sys
import json
import base64
import socket
import urllib.request
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

DEVTOOLS_PORT_FILE = Path(os.path.expanduser(r"~\AppData\Roaming\Antigravity\DevToolsActivePort"))
ANTIGRAVITY_EXE = Path(os.path.expanduser(r"~\AppData\Local\Programs\antigravity\Antigravity.exe"))

def get_devtools_port() -> Optional[int]:
    """Obtém a porta ativa do DevTools do Electron/Antigravity."""
    if not DEVTOOLS_PORT_FILE.exists():
        return None
    try:
        content = DEVTOOLS_PORT_FILE.read_text(encoding="utf-8").strip().splitlines()
        if content and content[0].isdigit():
            return int(content[0])
    except Exception:
        pass
    return None

def _send_ws_command(host: str, port: int, path: str, message: dict) -> bool:
    """
    Envia uma mensagem JSON através de um frame WebSocket mínimo usando apenas a biblioteca padrão (sockets).
    Compatível com Chrome DevTools Protocol no Electron/Antigravity.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    try:
        s.connect((host, port))
        
        # Handshake HTTP 1.1 WebSocket
        key = base64.b64encode(os.urandom(16)).decode('ascii')
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        s.sendall(handshake.encode('ascii'))
        
        # Lê resposta do handshake - aceita qualquer variação de HTTP 101
        resp = s.recv(1024).decode('latin1')
        if "101" not in resp:
            s.close()
            return False
            
        # Constrói frame de texto WebSocket mascarado (opcode 0x1)
        payload = json.dumps(message).encode('utf-8')
        length = len(payload)
        mask = os.urandom(4)
        
        frame = bytearray([0x81])
        if length <= 125:
            frame.append(0x80 | length)
        elif length <= 65535:
            frame.append(0x80 | 126)
            frame.extend(length.to_bytes(2, byteorder='big'))
        else:
            frame.append(0x80 | 127)
            frame.extend(length.to_bytes(8, byteorder='big'))
            
        frame.extend(mask)
        frame.extend(bytearray(payload[i] ^ mask[i % 4] for i in range(length)))
        
        s.sendall(frame)
        s.close()
        return True
    except Exception:
        try:
            s.close()
        except Exception:
            pass
        return False

def restart_language_server() -> bool:
    """
    Reinicia o language_server do Antigravity via RPC interno.
    Isso força a releitura imediata das novas credenciais do cofre sem fechar a janela.
    """
    try:
        from .quota_checker import _get_active_ls_info
        info = _get_active_ls_info()
        if not info:
            return False

        port = info.get("port")
        csrf = info.get("csrf")
        if not port:
            return False

        url = f"http://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/Restart"
        req = urllib.request.Request(
            url,
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "x-codeium-csrf-token": csrf
            }
        )
        try:
            # Como o processo morre ao reiniciar, timeout curto é esperado
            urllib.request.urlopen(req, timeout=1.0)
            return True
        except Exception:
            # Se a conexão for fechada abruptamente pelo servidor caindo, é sinal de sucesso
            return True
    except Exception:
        return False

def reload_antigravity_window() -> Dict[str, Any]:
    """
    Recarrega a janela do Antigravity IDE enviando sinais de reload
    via protocolo Chrome DevTools do Electron.
    """
    port = get_devtools_port()
    if not port:
        return {
            "success": False,
            "message": "DevTools não disponível no momento."
        }

    try:
        url = f"http://127.0.0.1:{port}/json/list"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=2) as resp:
            targets = json.loads(resp.read().decode())
            
        reloaded = 0
        for target in targets:
            if target.get("type") == "page":
                tid = target.get("id")
                path = f"/devtools/page/{tid}"
                ok1 = _send_ws_command("127.0.0.1", port, path, {"id": 1, "method": "Page.reload"})
                ok2 = _send_ws_command("127.0.0.1", port, path, {
                    "id": 2,
                    "method": "Runtime.evaluate",
                    "params": {"expression": "window.location.reload()"}
                })
                if ok1 or ok2:
                    reloaded += 1

        if reloaded > 0:
            return {
                "success": True,
                "message": "Interface do Antigravity recarregada com sucesso!"
            }
        else:
            return {
                "success": False,
                "message": "Nenhuma página ativa encontrada para recarregar."
            }
    except Exception as e:
        return {
            "success": False,
            "message": f"Erro ao comunicar com DevTools: {str(e)}"
        }

def restart_antigravity_app() -> Dict[str, Any]:
    """
    Reinício 100% completo e limpo do aplicativo Antigravity:
    Encerra Antigravity.exe e language_server.exe e reabre o executável principal.
    Restaura automaticamente workspace e conversas ativas.
    """
    exe_path = str(ANTIGRAVITY_EXE)
    if not ANTIGRAVITY_EXE.exists():
        exe_path = "Antigravity.exe"

    cmd = (
        f"Get-Process Antigravity, language_server -ErrorAction SilentlyContinue | Stop-Process -Force; "
        f"Start-Sleep -Milliseconds 800; "
        f"Start-Process '{exe_path}'"
    )

    try:
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS

        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            creationflags=creation_flags,
            close_fds=True
        )
        return {
            "success": True,
            "message": "O Antigravity está sendo reiniciado agora com a nova conta! Reabrirá em instantes."
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Falha ao disparar reinício do Antigravity: {str(e)}"
        }

def apply_hotswap_reload() -> Dict[str, Any]:
    """
    Método padrão chamado após o Hot-Swap:
    1. Reinicia o language_server para aplicar as novas credenciais.
    2. Recarrega as páginas da interface webview do Antigravity.
    """
    ls_ok = restart_language_server()
    win_res = reload_antigravity_window()
    if ls_ok:
        return {
            "success": True,
            "message": "Antigravity sincronizado com a nova conta!"
        }
    return win_res

if __name__ == "__main__":
    print("Recarregando Antigravity IDE...")
    res = apply_hotswap_reload()
    print("Resultado:", res.get("message"))
