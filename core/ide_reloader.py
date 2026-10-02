import os
import sys
import json
import base64
import socket
import urllib.request
from pathlib import Path
from typing import Dict, Any, Optional

DEVTOOLS_PORT_FILE = Path(os.path.expanduser(r"~\AppData\Roaming\Antigravity\DevToolsActivePort"))

def get_devtools_port() -> Optional[int]:
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

def reload_antigravity_window() -> Dict[str, Any]:
    """
    Recarrega a janela do Antigravity IDE enviando sinais de reload
    via protocolo Chrome DevTools do Electron.
    """
    port = get_devtools_port()
    if not port:
        return {
            "success": False,
            "message": "Porta do Antigravity não encontrada. Pressione F1 no Antigravity e digite 'Reload Window'."
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
                # 1. Envia comando Page.reload
                ok1 = _send_ws_command("127.0.0.1", port, path, {"id": 1, "method": "Page.reload"})
                # 2. Envia também window.location.reload() para garantir atualização do renderer
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
                "message": f"Janela do Antigravity recarregada com sucesso!"
            }
        else:
            return {
                "success": False,
                "message": "Não foi possível enviar o sinal. Pressione F1 no Antigravity e digite 'Reload Window'."
            }
    except Exception as e:
        return {
            "success": False,
            "message": f"Erro ao conectar com Antigravity: {str(e)}. Use F1 -> 'Reload Window'."
        }

if __name__ == "__main__":
    print("Enviando sinal de reload para o Antigravity IDE...")
    res = reload_antigravity_window()
    print("Resultado:", res.get("message"))
