import os
import sys
import time
import json
import base64
import socket
import threading
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

def _recv_exact(s: socket.socket, n: int) -> bytearray:
    """Lê exatamente n bytes de um socket, tratando fragmentação TCP no Windows."""
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return buf

def _read_ws_frame(s: socket.socket, expected_id: Optional[int] = None) -> Optional[dict]:
    """Lê frames WebSocket completos e retorna o payload JSON correspondente."""
    while True:
        hdr = _recv_exact(s, 2)
        if len(hdr) < 2:
            return None
        opcode = hdr[0] & 0x0F
        is_masked = bool(hdr[1] & 0x80)
        plen = hdr[1] & 0x7F
        if plen == 126:
            ext = _recv_exact(s, 2)
            if len(ext) < 2:
                return None
            plen = int.from_bytes(ext, "big")
        elif plen == 127:
            ext = _recv_exact(s, 8)
            if len(ext) < 8:
                return None
            plen = int.from_bytes(ext, "big")
        mask = _recv_exact(s, 4) if is_masked else None
        data = _recv_exact(s, plen)
        if len(data) < plen:
            return None
        if is_masked and mask:
            data = bytearray(data[i] ^ mask[i % 4] for i in range(len(data)))
        if opcode == 0x1:  # Text frame
            try:
                parsed = json.loads(data.decode("utf-8", errors="ignore"))
                if expected_id is None or parsed.get("id") == expected_id:
                    return parsed
            except Exception:
                return None
        elif opcode == 0x8:  # Close frame
            return None

def _build_ws_frame(message: dict) -> bytearray:
    """Constrói frame WebSocket mascarado com payload JSON."""
    payload = json.dumps(message).encode("utf-8")
    length = len(payload)
    mask = os.urandom(4)
    frame = bytearray([0x81])
    if length <= 125:
        frame.append(0x80 | length)
    elif length <= 65535:
        frame.append(0x80 | 126)
        frame.extend(length.to_bytes(2, byteorder="big"))
    else:
        frame.append(0x80 | 127)
        frame.extend(length.to_bytes(8, byteorder="big"))
    frame.extend(mask)
    frame.extend(bytearray(payload[i] ^ mask[i % 4] for i in range(length)))
    return frame

def _send_ws_command(host: str, port: int, path: str, message: dict) -> bool:
    """Envia um comando WebSocket via Chrome DevTools Protocol."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.5)
    try:
        s.connect((host, port))
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        s.sendall(handshake.encode("ascii"))
        resp = s.recv(1024).decode("latin1")
        if "101" not in resp:
            s.close()
            return False

        s.sendall(_build_ws_frame(message))
        s.close()
        return True
    except Exception:
        try:
            s.close()
        except Exception:
            pass
        return False

def _eval_ws_expression(host: str, port: int, path: str, expression: str, timeout: float = 4.0) -> Optional[Any]:
    """
    Executa uma expressão JavaScript via DevTools WebSocket e retorna o valor resultante.
    Suporta Promises e montagem completa de frames fragmentados no Windows.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        s.sendall(handshake.encode("ascii"))
        resp = s.recv(1024).decode("latin1")
        if "101" not in resp:
            s.close()
            return None

        cmd_id = int(time.time() * 1000) % 100000
        message = {
            "id": cmd_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True
            }
        }
        s.sendall(_build_ws_frame(message))
        resp_json = _read_ws_frame(s, expected_id=cmd_id)
        s.close()

        if resp_json:
            return resp_json.get("result", {}).get("result", {}).get("value")
        return None
    except Exception:
        try:
            s.close()
        except Exception:
            pass
        return None

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

def send_continue_to_antigravity(prompt_text: str = "continue", max_wait_seconds: float = 20.0) -> Dict[str, Any]:
    """
    Injeta e envia automaticamente uma mensagem no chat do Google Antigravity.
    Monitora a interface até que o chat esteja pronto e desocupado para disparar o envio.
    Compatível com Meta Lexical editor e Chrome DevTools Protocol.
    """
    start_time = time.time()
    escaped_prompt = json.dumps(prompt_text)

    js_code = f"""
    (async () => {{
        const editor = document.querySelector('[contenteditable="true"], [aria-label="Message input"]');
        if (!editor) return {{ status: 'waiting_editor' }};

        const card = editor.closest('.bg-card') || editor.parentElement;
        if (!card) return {{ status: 'waiting_card' }};

        const cancelBtn = card.querySelector('button[aria-label*="Cancel"]');
        if (cancelBtn) return {{ status: 'still_busy' }};

        editor.focus();

        const span = editor.querySelector('span[data-lexical-text="true"]');
        const sel = window.getSelection();
        sel.removeAllRanges();
        const range = document.createRange();
        if (span) {{
            range.selectNodeContents(span);
        }} else {{
            range.selectNodeContents(editor);
            range.collapse(false);
        }}
        sel.addRange(range);

        document.execCommand('insertText', false, {escaped_prompt});
        editor.dispatchEvent(new Event('input', {{ bubbles: true }}));

        await new Promise(r => setTimeout(r, 120));

        const buttons = Array.from(card.querySelectorAll('button'));
        const sendBtn = card.querySelector('[data-testid="send-button"], button[aria-label*="Send"], button[aria-label*="Enviar"]')
            || buttons.find(b => {{
                const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                return !aria.includes('context') && !aria.includes('model') && !aria.includes('voice') && !aria.includes('memo') && !aria.includes('cancel');
            }});

        if (sendBtn && !sendBtn.disabled) {{
            sendBtn.click();
        }}

        const enterEv = new KeyboardEvent('keydown', {{
            key: 'Enter',
            code: 'Enter',
            keyCode: 13,
            which: 13,
            bubbles: true,
            cancelable: true
        }});
        editor.dispatchEvent(enterEv);

        await new Promise(r => setTimeout(r, 150));

        return {{ status: 'sent' }};
    }})()
    """

    while time.time() - start_time < max_wait_seconds:
        port = get_devtools_port()
        if not port:
            time.sleep(0.5)
            continue

        try:
            url = f"http://127.0.0.1:{port}/json/list"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                targets = json.loads(resp.read().decode())

            pages = [t for t in targets if t.get("type") == "page"]
            if not pages:
                time.sleep(0.5)
                continue

            for page in pages:
                tid = page.get("id")
                if not tid:
                    continue
                path = f"/devtools/page/{tid}"
                res = _eval_ws_expression("127.0.0.1", port, path, js_code)
                if isinstance(res, dict):
                    st = res.get("status")
                    if st == "sent":
                        return {
                            "success": True,
                            "message": f"Comando '{prompt_text}' injetado com sucesso no Antigravity!"
                        }
                    elif st == "still_busy":
                        break
        except Exception:
            pass

        time.sleep(0.6)

    return {
        "success": False,
        "message": "Tempo esgotado aguardando interface do Antigravity ficar pronta."
    }

def schedule_auto_continue(delay_seconds: float = 3.0, prompt_text: str = "continue"):
    """
    Agenda o envio do 'continue' em processo desacoplado (detached process),
    garantindo que sobreviva mesmo se o processo chamador ou o Language Server for encerrado.
    """
    base_dir = Path(__file__).resolve().parent.parent
    escaped_prompt = json.dumps(prompt_text)
    cmd = (
        f"import time; "
        f"time.sleep({delay_seconds}); "
        f"from core.ide_reloader import send_continue_to_antigravity; "
        f"send_continue_to_antigravity(prompt_text={escaped_prompt})"
    )

    try:
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS

        subprocess.Popen(
            [sys.executable, "-c", cmd],
            cwd=str(base_dir),
            creationflags=creation_flags,
            close_fds=True
        )
    except Exception:
        def _worker():
            time.sleep(delay_seconds)
            send_continue_to_antigravity(prompt_text=prompt_text)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

def restart_antigravity_app(auto_continue: bool = True, continue_delay: float = 4.5) -> Dict[str, Any]:
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
        if auto_continue:
            schedule_auto_continue(delay_seconds=continue_delay)

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
            "message": "O Antigravity está sendo reiniciado agora com a nova conta! O agente continuará automaticamente em instantes."
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Falha ao disparar reinício do Antigravity: {str(e)}"
        }

def apply_hotswap_reload(auto_continue: bool = True, continue_delay: float = 3.0) -> Dict[str, Any]:
    """
    Método padrão chamado após o Hot-Swap:
    1. Se auto_continue=True, agenda o envio desacoplado de 'continue'.
    2. Reinicia o language_server para aplicar as novas credenciais.
    3. Recarrega as páginas da interface webview do Antigravity.
    """
    if auto_continue:
        schedule_auto_continue(delay_seconds=continue_delay)

    ls_ok = restart_language_server()
    win_res = reload_antigravity_window()

    if ls_ok:
        return {
            "success": True,
            "message": "Antigravity sincronizado com a nova conta! (Auto-Continue agendado)"
        }
    return win_res

if __name__ == "__main__":
    print("Testando injeção de continue...")
    res = send_continue_to_antigravity()
    print("Resultado:", res.get("message"))
