import os
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

def _send_ws_json_message(host: str, port: int, path: str, message: dict) -> bool:
    """
    Envia uma mensagem JSON através de um frame WebSocket mínimo usando apenas a biblioteca padrão (sockets).
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
        
        # Lê resposta do handshake
        resp = s.recv(1024).decode('latin1')
        if "101 Switching Protocols" not in resp:
            s.close()
            return False
            
        # Constrói frame de texto WebSocket (opcode 0x1, masked)
        payload = json.dumps(message).encode('utf-8')
        length = len(payload)
        mask = os.urandom(4)
        
        # Byte 1: FIN (0x80) | Text Opcode (0x01) = 0x81
        frame = bytearray([0x81])
        
        # Byte 2: Masked (0x80) | length
        if length <= 125:
            frame.append(0x80 | length)
        elif length <= 65535:
            frame.append(0x80 | 126)
            frame.extend(length.to_bytes(2, byteorder='big'))
        else:
            frame.append(0x80 | 127)
            frame.extend(length.to_bytes(8, byteorder='big'))
            
        frame.extend(mask)
        masked_payload = bytearray(payload[i] ^ mask[i % 4] for i in range(length))
        frame.extend(masked_payload)
        
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
    Recarrega a janela do Antigravity IDE enviando o comando 'Page.reload'
    via protocolo Chrome DevTools.
    """
    port = get_devtools_port()
    if not port:
        return {
            "success": False,
            "message": "Janela do Antigravity não encontrada ou porta DevTools indisponível. Pressione Ctrl+Shift+P -> 'Developer: Reload Window'."
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
                ok = _send_ws_json_message("127.0.0.1", port, path, {"id": 1, "method": "Page.reload"})
                if ok:
                    reloaded += 1

        if reloaded > 0:
            return {
                "success": True,
                "message": f"Janela do Antigravity recarregada com sucesso ({reloaded} janela(s) atualizada(s))!"
            }
        else:
            return {
                "success": False,
                "message": "Não foi possível enviar o sinal de reload. Pressione Ctrl+Shift+P no Antigravity e escolha 'Reload Window'."
            }
    except Exception as e:
        return {
            "success": False,
            "message": f"Falha ao recarregar Antigravity: {str(e)}. Use Ctrl+Shift+P -> 'Developer: Reload Window'."
        }
