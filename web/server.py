import os
import sys
import json
import socket
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# Adiciona o pacote core ao path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.vault import AccountsVault
from core.switcher import AccountSwitcher
from core.oauth_capture import enrollment_manager
from core.ide_reloader import reload_antigravity_window, restart_antigravity_app, apply_hotswap_reload
from core.auto_detector import AutoQuotaDetector

PORT = 5055
WEB_DIR = Path(__file__).resolve().parent

class DualStackServer(ThreadingHTTPServer):
    """
    Servidor HTTP Multi-threaded com suporte Dual-Stack (IPv4 e IPv6).
    Garante resposta instantânea tanto em http://localhost quanto em http://127.0.0.1.
    """
    address_family = socket.AF_INET6

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except Exception:
            pass
        super().server_bind()

class HotswapHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    vault = AccountsVault()
    switcher = AccountSwitcher(vault=vault)
    detector = AutoQuotaDetector(vault=vault, switcher=switcher)

    def log_message(self, format, *args):
        # Silencia logs no terminal para manter console legível
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path.startswith("/index"):
            index_path = WEB_DIR / "index.html"
            if index_path.exists():
                html_bytes = index_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_bytes)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(html_bytes)
            else:
                self.send_error(404, "index.html não encontrado")
        elif path == "/api/status":
            status_data = self.switcher.get_status()
            raw_vault = self.vault._load()
            status_data["history"] = raw_vault.get("history", [])[:30]
            self._send_json(status_data)
        elif path == "/api/accounts/enroll_status":
            st = enrollment_manager.get_status()
            self._send_json(st)
        else:
            self.send_error(404, "Endpoint não encontrado")

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        parsed = urlparse(self.path)
        path = parsed.path

        # 1. Hot-Swap de Conta
        if path == "/api/switch":
            account_id = payload.get("account_id")
            if not account_id:
                self._send_json({"success": False, "message": "ID da conta é obrigatório."}, 400)
                return
            res = self.switcher.switch_to_account(account_id)
            self._send_json(res)

        # 2. Reinício Completo do Antigravity (1-Clique)
        elif path == "/api/restart_ide":
            res = restart_antigravity_app()
            self._send_json(res)

        # 3. Recarregar / Sincronizar Antigravity sem fechar janela
        elif path == "/api/reload_ide":
            res = apply_hotswap_reload()
            self._send_json(res)

        # 4. Cadastro Automático de Nova Conta Google
        elif path == "/api/accounts/enroll":
            name = payload.get("name", "Nova Conta Google")
            res = enrollment_manager.start_enrollment(name)
            self._send_json(res)

        elif path == "/api/accounts/cancel_enroll":
            res = enrollment_manager.cancel_enrollment()
            self._send_json(res)

        # 5. Capturar Conta do Windows (Manual)
        elif path == "/api/accounts/capture_active":
            name = payload.get("name", "Conta Capturada")
            email = payload.get("email", "")
            res = self.switcher.capture_active_wincred(name=name, email=email)
            self._send_json(res)

        # 6. Excluir Conta
        elif path == "/api/accounts/delete":
            account_id = payload.get("account_id")
            success = self.vault.remove_account(account_id)
            self._send_json({"success": success, "message": "Conta removida do cofre." if success else "Falha ao remover conta."})

        # 7. Reset Manual de Cooldown
        elif path == "/api/reset_cooldown":
            account_id = payload.get("account_id")
            acc = self.vault.clear_account_cooldown(account_id)
            if acc:
                self.vault.log_history("COOLDOWN_CLEARED", f"Cota liberada manualmente para '{acc.get('name')}'.")
                self._send_json({"success": True, "message": f"Cota liberada para '{acc.get('name')}'!", "account": acc})
            else:
                self._send_json({"success": False, "message": "Conta não encontrada."}, 404)

        else:
            self.send_error(404, "Endpoint não encontrado")

def run(port: int = PORT):
    HotswapHandler.detector.start_background_scanner(interval=5)
    try:
        httpd = DualStackServer(("::", port), HotswapHandler)
    except Exception:
        # Fallback IPv4 puro se a máquina não suportar IPv6
        httpd = ThreadingHTTPServer(("0.0.0.0", port), HotswapHandler)

    httpd.daemon_threads = True
    print("=" * 60)
    print(f"  Gerenciador de Hot-Swap de Contas Antigravity")
    print(f"  Cotas em Tempo Real: ATIVAS")
    print(f"  Servidor HTTP Multi-threaded Dual-Stack na porta {port}")
    print(f"  Acesse no Navegador: http://localhost:{port} ou http://127.0.0.1:{port}")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        HotswapHandler.detector.stop_background_scanner()
        print("\nServidor encerrado.")

if __name__ == "__main__":
    run()
