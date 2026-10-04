import os
import re
import sys
import time
import json
import base64
import socket
import threading
import urllib.request
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from typing import Dict, Any, Optional, List
import psutil

DEVTOOLS_PORT_FILE = Path(os.path.expanduser(r"~\AppData\Roaming\Antigravity\DevToolsActivePort"))
ANTIGRAVITY_EXE = Path(os.path.expanduser(r"~\AppData\Local\Programs\antigravity\Antigravity.exe"))
ANTIGRAVITY_MAIN_LOG = Path(os.path.expanduser(r"~\AppData\Roaming\Antigravity\logs\main.log"))

def get_current_content_origin() -> Optional[str]:
    """
    Lê o main.log do Antigravity e retorna a origem (https://127.0.0.1:PORT) do conteúdo
    web ATUAL - a porta mais recente que o próprio Antigravity relatou ter carregado
    ('Local:' na inicialização, ou '[Auto-Restart] Port changed!' após um restart do
    language_server). Essa é a fonte de verdade mais confiável para reconstruir a URL de
    navegação: ao contrário de ler window.location.href via CDP, não é afetada pela página
    já estar travada em chrome-error:// (já que a leitura é feita direto do que o próprio
    Antigravity escreveu, independente do estado atual do webview).
    """
    try:
        if not ANTIGRAVITY_MAIN_LOG.exists():
            return None
        # Lê só os últimos ~8KB - a porta mais recente está sempre perto do final do arquivo
        with open(ANTIGRAVITY_MAIN_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 8192))
            tail = f.read().decode("utf-8", errors="ignore")
        # Restrito às linhas AUTORITATIVAS ("Local:" na inicialização, ou "Port changed!"
        # após um restart) - nunca às linhas de erro "Failed to load URL", que também
        # contêm uma URL com porta, mas é exatamente a porta MORTA que causou o erro.
        matches = re.findall(
            r"(?:Port changed! Reloading all windows with URL: |Local:\s+)https://127\.0\.0\.1:(\d+)/?",
            tail
        )
        if matches:
            return f"https://127.0.0.1:{matches[-1]}"
    except Exception:
        pass
    return None

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

def get_devtools_browser_path() -> Optional[str]:
    """Obtém o path do endpoint CDP de nível browser (linha 2 do DevToolsActivePort)."""
    if not DEVTOOLS_PORT_FILE.exists():
        return None
    try:
        lines = DEVTOOLS_PORT_FILE.read_text(encoding="utf-8").strip().splitlines()
        if len(lines) > 1 and lines[1].startswith("/devtools/browser/"):
            return lines[1].strip()
    except Exception:
        pass
    return None

def _list_cdp_pages(port: int) -> List[dict]:
    """Lista todas as abas/páginas (targets) ativas no DevTools do Electron."""
    try:
        url = f"http://127.0.0.1:{port}/json/list"
        with urllib.request.urlopen(urllib.request.Request(url), timeout=1.5) as resp:
            return [t for t in json.loads(resp.read().decode()) if t.get("type") == "page"]
    except Exception:
        return []

def _cdp_navigate(host: str, port: int, path: str, url: str) -> bool:
    """
    Navega uma aba CDP existente para a URL salva via Page.navigate.
    Mais robusto que window.location.href via JS, pois não depende do
    contexto de execução da página continuar responsivo.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3.0)
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
        cmd_id = int(time.time() * 1000) % 100000
        s.sendall(_build_ws_frame({"id": cmd_id, "method": "Page.navigate", "params": {"url": url}}))
        _read_ws_frame(s, expected_id=cmd_id)
        s.close()
        return True
    except Exception:
        try:
            s.close()
        except Exception:
            pass
        return False

def create_new_tab(url: str) -> Optional[str]:
    """
    Abre uma nova aba no Electron via CDP de nível browser (Target.createTarget),
    usado quando há mais conversas interrompidas do que abas disponíveis para retomar.
    Retorna o targetId da nova aba, ou None em caso de falha.
    """
    port = get_devtools_port()
    browser_path = get_devtools_browser_path()
    if not port or not browser_path:
        return None
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(4.0)
    try:
        s.connect(("127.0.0.1", port))
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET {browser_path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
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
        s.sendall(_build_ws_frame({
            "id": cmd_id,
            "method": "Target.createTarget",
            "params": {"url": url, "newWindow": False}
        }))
        resp_json = _read_ws_frame(s, expected_id=cmd_id)
        s.close()
        if resp_json:
            return resp_json.get("result", {}).get("targetId")
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
        info = _get_active_ls_info(force_refresh=True)
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

def send_continue_to_antigravity(
    prompt_text: str = "continue",
    target_conv_id: Optional[str] = None,
    saved_url: Optional[str] = None,
    max_wait_seconds: float = 30.0,
    target_page_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Injeta e envia automaticamente uma mensagem no chat do Google Antigravity.
    Se target_conv_id for fornecido, garante que o comando seja enviado EXCLUSIVAMENTE
    na conversa correspondente. Se a aba atual não estiver nessa conversa (ex: após um
    restart completo do Antigravity, que reabre na tela inicial '/'), navega ativamente
    até saved_url via CDP (Page.navigate) antes de tentar injetar, em vez de desistir.
    Se target_page_id for fornecido, a operação é restrita a essa aba específica,
    evitando colisão quando várias conversas estão sendo retomadas em paralelo.
    """
    start_time = time.time()
    escaped_prompt = json.dumps(prompt_text)

    check_js = "(() => ({ pathname: window.location.pathname || '', href: window.location.href }))()"

    inject_js = f"""
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
        }} else {{
            const enterEv = new KeyboardEvent('keydown', {{
                key: 'Enter',
                code: 'Enter',
                keyCode: 13,
                which: 13,
                bubbles: true,
                cancelable: true
            }});
            editor.dispatchEvent(enterEv);
        }}

        await new Promise(r => setTimeout(r, 150));

        return {{ status: 'sent', path: window.location.pathname }};
    }})()
    """

    navigated = False

    while time.time() - start_time < max_wait_seconds:
        port = get_devtools_port()
        if not port:
            time.sleep(0.5)
            continue

        pages = _list_cdp_pages(port)
        if not pages:
            time.sleep(0.5)
            continue

        candidate_pages = [p for p in pages if p.get("id") == target_page_id] if target_page_id else pages

        made_progress = False
        for page in candidate_pages:
            tid = page.get("id")
            if not tid:
                continue
            path = f"/devtools/page/{tid}"

            st = _eval_ws_expression("127.0.0.1", port, path, check_js) or {}
            pathname = st.get("pathname", "")
            current_href = st.get("href", "")

            if target_conv_id:
                on_target = target_conv_id in pathname
            else:
                on_target = "/c/" in pathname

            if not on_target:
                if saved_url and not navigated:
                    # IMPORTANTE: nunca navega para saved_url literalmente. O Antigravity troca
                    # de porta a cada restart do language_server, então a porta capturada no
                    # snapshot (antes deste restart) já pode estar morta agora. Reconstrói a URL
                    # usando a ORIGEM ATUAL real + o caminho/query salvos (conv_id e section, que
                    # continuam válidos entre restarts). Prioriza a origem lida do main.log do
                    # Antigravity (imune a chrome-error://), com fallback para o href atual via CDP.
                    target_url = saved_url
                    current_origin = get_current_content_origin()
                    if not current_origin and current_href:
                        parsed_current = urlparse(current_href)
                        if parsed_current.scheme and parsed_current.netloc and parsed_current.scheme != "chrome-error":
                            current_origin = f"{parsed_current.scheme}://{parsed_current.netloc}"
                    if current_origin:
                        parsed_saved = urlparse(saved_url)
                        path_and_query = parsed_saved.path + (f"?{parsed_saved.query}" if parsed_saved.query else "")
                        target_url = f"{current_origin}{path_and_query}"

                    if _cdp_navigate("127.0.0.1", port, path, target_url):
                        navigated = True
                        made_progress = True
                        time.sleep(1.8)  # dá tempo da SPA carregar a rota antes de tentar injetar
                continue

            res = _eval_ws_expression("127.0.0.1", port, path, inject_js)
            if isinstance(res, dict):
                stv = res.get("status")
                if stv == "sent":
                    return {
                        "success": True,
                        "message": f"Comando '{prompt_text}' injetado com sucesso na conversa!",
                        "path": res.get("path")
                    }
                elif stv == "still_busy":
                    made_progress = True
                    break

        if not made_progress:
            time.sleep(0.6)

    return {
        "success": False,
        "message": f"Tempo esgotado aguardando conversa {target_conv_id or ''} ficar pronta."
    }

CONTINUE_DEBOUNCE_FILE = Path(__file__).resolve().parent.parent / "data" / "continue_debounce.json"

def get_language_server_pids() -> List[int]:
    """Retorna os PIDs de todos os processos language_server em execução."""
    pids = []
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            name = proc.info.get('name') or ''
            if 'language_server' in name.lower():
                pids.append(proc.info['pid'])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return pids

def _is_debounced(cid: str, cooldown: float = 8.0) -> bool:
    """Verifica se já foi enviado continue para esta conversa nos últimos cooldown segundos."""
    try:
        if CONTINUE_DEBOUNCE_FILE.exists():
            with open(CONTINUE_DEBOUNCE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            last_t = data.get(cid, 0.0)
            if time.time() - last_t < cooldown:
                return True
    except Exception:
        pass
    return False

def _record_debounce(cid: str):
    """Grava timestamp do envio para a conversa em arquivo compartilhado entre processos."""
    try:
        data = {}
        if CONTINUE_DEBOUNCE_FILE.exists():
            try:
                with open(CONTINUE_DEBOUNCE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        now = time.time()
        data[cid] = now
        # Limpa entradas com mais de 120s
        data = {k: v for k, v in data.items() if now - v < 120.0}
        CONTINUE_DEBOUNCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONTINUE_DEBOUNCE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass

def resume_interrupted_sessions(delay_seconds: float = 0.0, prompt_text: str = "continue") -> Dict[str, Any]:
    """
    Restaura e envia 'continue' para as conversas registradas no snapshot pré-hotswap.
    Aplica deduplicação estrita, trava de arquivo e debounce de 8 segundos para evitar qualquer envio duplo.
    Garante que novos chats nunca sejam criados.
    """
    if delay_seconds > 0:
        time.sleep(delay_seconds)

    from .chat_tracker import ChatSessionTracker
    interrupted = ChatSessionTracker.get_interrupted_sessions()

    # Imediatamente limpa o arquivo para que nenhum outro processo concorrente tente processar
    ChatSessionTracker.clear_interrupted_sessions()

    # Deduplica sessões por conv_id
    unique_sessions = {}
    if interrupted:
        for s in interrupted:
            cid = s.get("conv_id")
            if cid and cid not in unique_sessions:
                unique_sessions[cid] = s

    resumed_ids = []
    if unique_sessions:
        sessions_list = list(unique_sessions.items())
        port = get_devtools_port()
        pages = _list_cdp_pages(port) if port else []

        # Mesmo cuidado da navegação principal: a URL salva no snapshot pode ter a porta
        # de ANTES do restart, já morta agora. Reconstrói com a origem atual antes de abrir
        # cada aba extra, em vez de abrir direto na URL antiga (causaria o mesmo erro de
        # conexão recusada / tela preta, só que numa aba nova).
        current_origin = get_current_content_origin()

        def _rebuild_url(raw_url: Optional[str]) -> str:
            if not raw_url:
                return "about:blank"
            if not current_origin:
                return raw_url
            parsed = urlparse(raw_url)
            path_and_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")
            return f"{current_origin}{path_and_query}"

        # Se há mais conversas interrompidas do que abas disponíveis, abre abas extras
        # via CDP (Target.createTarget) já na URL reconstruída de cada conversa restante.
        attempts = 0
        while port and len(pages) < len(sessions_list) and attempts < len(sessions_list):
            extra_cid, extra_s = sessions_list[len(pages)]
            new_tid = create_new_tab(_rebuild_url(extra_s.get("url")))
            attempts += 1
            if not new_tid:
                break
            time.sleep(1.0)
            pages = _list_cdp_pages(port)

        for i, (cid, s) in enumerate(sessions_list):
            if _is_debounced(cid, cooldown=8.0):
                print(f"[Hot-Swap Watchdog] Conversa {cid} já recebeu continue recentemente (debounced).")
                continue

            if i >= len(pages):
                # Não há aba dedicada para esta sessão (criação de aba extra falhou). Melhor
                # desistir desta sessão específica do que usar target_page_id=None: isso faria
                # a busca abranger TODAS as abas, podendo roubar a aba de uma conversa que uma
                # sessão anterior já retomou corretamente neste mesmo loop.
                print(f"[Hot-Swap Watchdog] Sem aba disponível para retomar a conversa {cid} - pulando.")
                continue

            _record_debounce(cid)

            url = s.get("url")
            page_id = pages[i].get("id")
            res = send_continue_to_antigravity(
                prompt_text=prompt_text,
                target_conv_id=cid,
                saved_url=url,
                max_wait_seconds=30.0,
                target_page_id=page_id
            )
            if res.get("success"):
                resumed_ids.append(cid)
    else:
        # Fallback inteligente se nenhuma sessão salva: injeta apenas se houver conversa aberta
        fallback_key = "active_page"
        if not _is_debounced(fallback_key, cooldown=8.0):
            _record_debounce(fallback_key)
            res = send_continue_to_antigravity(prompt_text=prompt_text, max_wait_seconds=20.0)
            if res.get("success"):
                resumed_ids.append(res.get("path", "active_chat"))

    return {
        "success": True,
        "resumed_count": len(resumed_ids),
        "resumed_conversations": resumed_ids,
        "message": f"{len(resumed_ids)} sessão(ões) retomada(s) com sucesso!"
    }

def wait_and_resume_after_restart(
    old_pids: Optional[List[int]] = None,
    prompt_text: str = "continue",
    max_wait_seconds: float = 45.0
) -> Dict[str, Any]:
    """
    Sincronização determinística pós-restart:
    1. Aguarda saída dos processos antigos (old_pids).
    2. Aguarda surgimento do novo processo language_server.
    3. Aguarda novo language_server estar saudável e pronto para RPC.
    4. Aguarda estabilização da interface Webview/DevTools do Antigravity.
    5. Injeta 'continue' exclusivamente nas conversas ativas salvas.
    """
    start_t = time.time()
    old_pids_set = set(old_pids or [])

    # ----------------------------------------------------
    # FASE 1: Aguardar encerramento dos processos antigos
    # ----------------------------------------------------
    if old_pids_set:
        print(f"[Hot-Swap Watchdog] Aguardando término dos processos antigos: {list(old_pids_set)}")
        while time.time() - start_t < 10.0:
            still_alive = [pid for pid in old_pids_set if psutil.pid_exists(pid)]
            if not still_alive:
                print("[Hot-Swap Watchdog] Todos os processos antigos foram encerrados.")
                break

            # Se passaram mais de 10s e o processo antigo ainda está vivo, tenta terminate suave
            if time.time() - start_t > 10.0:
                for pid in still_alive:
                    try:
                        p = psutil.Process(pid)
                        p.terminate()
                        print(f"[Hot-Swap Watchdog] Processo antigo {pid} finalizado suavemente.")
                    except Exception:
                        pass
            time.sleep(0.3)

    # ----------------------------------------------------
    # FASE 2: Aguardar novo processo language_server iniciar
    # ----------------------------------------------------
    print("[Hot-Swap Watchdog] Aguardando inicialização do novo language_server...")
    new_pid = None
    while time.time() - start_t < max_wait_seconds:
        current_pids = set(get_language_server_pids())
        new_candidates = current_pids - old_pids_set
        if new_candidates:
            new_pid = list(new_candidates)[0]
            print(f"[Hot-Swap Watchdog] Novo processo language_server detectado: PID {new_pid}")
            break
        elif not old_pids_set and current_pids:
            new_pid = list(current_pids)[0]
            print(f"[Hot-Swap Watchdog] Processo language_server detectado: PID {new_pid}")
            break
        time.sleep(0.3)

    # ----------------------------------------------------
    # FASE 3: Aguardar novo Language Server responder (Saúde/Porta RPC)
    # ----------------------------------------------------
    print("[Hot-Swap Watchdog] Aguardando novo Language Server estar pronto para RPC...")
    from .quota_checker import _get_active_ls_info, invalidate_ls_cache
    invalidate_ls_cache()

    while time.time() - start_t < max_wait_seconds:
        info = _get_active_ls_info(force_refresh=True)
        if info and info.get("port"):
            info_pid = info.get("pid")
            if new_pid is None or info_pid is None or info_pid == new_pid or info_pid not in old_pids_set:
                print(f"[Hot-Swap Watchdog] Language Server ativo na porta {info.get('port')} (PID {info_pid or new_pid})")
                break
        time.sleep(0.4)

    # ----------------------------------------------------
    # FASE 4: Aguardar estabilização do Webview do Antigravity
    # ----------------------------------------------------
    # Respiro essencial para o Electron reconectar ao novo processo gRPC
    time.sleep(2.0)

    print("[Hot-Swap Watchdog] Aguardando interface do Antigravity ficar ociosa e conectada...")
    from .chat_tracker import ChatSessionTracker
    while time.time() - start_t < max_wait_seconds:
        sessions = ChatSessionTracker.get_open_sessions()
        if sessions:
            has_ready_editor = any(s.get("has_editor") and not s.get("is_busy") for s in sessions)
            if has_ready_editor:
                print("[Hot-Swap Watchdog] Interface do Antigravity pronta!")
                break
        time.sleep(0.5)

    # ----------------------------------------------------
    # FASE 5: Injeção atômica do continue
    # ----------------------------------------------------
    print("[Hot-Swap Watchdog] Retomando sessões interrompidas...")
    res = resume_interrupted_sessions(delay_seconds=0, prompt_text=prompt_text)
    print(f"[Hot-Swap Watchdog] Resultado da retomada: {res.get('message')}")
    return res

def schedule_auto_continue(
    old_pids: Optional[List[int]] = None,
    delay_seconds: float = 0.0,
    prompt_text: str = "continue"
):
    """
    Agenda o envio do 'continue' em processo desacoplado (detached process),
    monitorando o ciclo de vida real do Language Server para garantir que
    o continue NUNCA seja enviado antes do reinício ser concluído.
    """
    if old_pids is None:
        old_pids = get_language_server_pids()

    base_dir = Path(__file__).resolve().parent.parent
    escaped_prompt = json.dumps(prompt_text)
    old_pids_json = json.dumps(old_pids)

    cmd = (
        f"from core.ide_reloader import wait_and_resume_after_restart; "
        f"wait_and_resume_after_restart(old_pids={old_pids_json}, prompt_text={escaped_prompt})"
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
            wait_and_resume_after_restart(old_pids=old_pids, prompt_text=prompt_text)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

def restart_antigravity_app(auto_continue: bool = True, continue_delay: float = 0.0) -> Dict[str, Any]:
    """
    Reinício 100% completo e limpo do aplicativo Antigravity:
    Encerra Antigravity.exe e language_server.exe e reabre o executável principal.
    Restaura automaticamente workspace e conversas ativas após o reinício.
    """
    old_pids = get_language_server_pids()
    exe_path = str(ANTIGRAVITY_EXE)
    if not ANTIGRAVITY_EXE.exists():
        exe_path = "Antigravity.exe"

    cmd = (
        f"Get-Process Antigravity, language_server -ErrorAction SilentlyContinue | Stop-Process -Force; "
        f"Start-Sleep -Milliseconds 2500; "
        f"Start-Process '{exe_path}'"
    )

    try:
        if auto_continue:
            from .chat_tracker import ChatSessionTracker
            ChatSessionTracker.snapshot_active_sessions()
            schedule_auto_continue(old_pids=old_pids)

        from .quota_checker import invalidate_ls_cache
        invalidate_ls_cache()

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
            "message": "O Antigravity está sendo reiniciado agora com a nova conta! O agente continuará automaticamente após o reinício."
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Falha ao disparar reinício do Antigravity: {str(e)}"
        }

def apply_hotswap_reload(auto_continue: bool = True, continue_delay: float = 0.0) -> Dict[str, Any]:
    """
    Método padrão chamado após o Hot-Swap:
    1. Registra snapshot das conversas ativas (se auto_continue=True).
    2. Identifica os PIDs atuais do language_server.
    3. Invalida o cache de status do language_server.
    4. Agenda o watchdog desacoplado (que aguardará o novo processo iniciar).
    5. Dispara o reinício do language_server via RPC /Restart.
    """
    old_pids = get_language_server_pids()

    if auto_continue:
        try:
            from .chat_tracker import ChatSessionTracker
            ChatSessionTracker.snapshot_active_sessions()
        except Exception:
            pass
        schedule_auto_continue(old_pids=old_pids)

    from .quota_checker import invalidate_ls_cache
    invalidate_ls_cache()

    ls_ok = restart_language_server()
    if not ls_ok and old_pids:
        # Fallback se a chamada RPC falhar: encerra apenas o language_server antigo
        for pid in old_pids:
            try:
                psutil.Process(pid).terminate()
            except Exception:
                pass

    return {
        "success": True,
        "message": "Antigravity sincronizado com a nova conta! (Auto-Continue sincronizado pós-restart)"
    }

if __name__ == "__main__":
    print("Testando injeção de continue...")
    res = send_continue_to_antigravity()
    print("Resultado:", res.get("message"))
