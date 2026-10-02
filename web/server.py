import os
import sys
import json
import threading
import subprocess
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# Adiciona o pacote core ao path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.vault import AccountsVault
from core.switcher import AccountSwitcher
from core.oauth_capture import enroll_new_google_account
from core.auto_detector import AutoQuotaDetector, parse_error_output

PORT = 5055
WEB_DIR = Path(__file__).resolve().parent

class HotswapHandler(BaseHTTPRequestHandler):
    vault = AccountsVault()
    switcher = AccountSwitcher(vault=vault)
    detector = AutoQuotaDetector(vault=vault, switcher=switcher)

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(200)
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
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(index_path.read_bytes())
            else:
                self.send_error(404, "index.html não encontrado")
        elif path == "/api/status":
            status_data = self.switcher.get_status()
            # Inclui o histórico recente
            raw_vault = self.vault._load()
            status_data["history"] = raw_vault.get("history", [])[:30]
            self._send_json(status_data)
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

        if path == "/api/switch":
            account_id = payload.get("account_id")
            if not account_id:
                self._send_json({"success": False, "message": "ID da conta é obrigatório."}, 400)
                return
            res = self.switcher.switch_to_account(account_id)
            self._send_json(res)

        elif path == "/api/report_exhaustion":
            quota_type = payload.get("quota_type", "5h")  # '5h' ou 'weekly'
            account_id = payload.get("account_id")
            custom_until = payload.get("custom_until")
            custom_seconds = None

            if "custom_hours" in payload and payload["custom_hours"] is not None:
                custom_seconds = int(float(payload["custom_hours"]) * 3600)
            elif "custom_minutes" in payload and payload["custom_minutes"] is not None:
                custom_seconds = int(float(payload["custom_minutes"]) * 60)
            elif "custom_seconds" in payload and payload["custom_seconds"] is not None:
                custom_seconds = int(payload["custom_seconds"])

            res = self.switcher.report_quota_and_swap(
                quota_type=quota_type,
                account_id=account_id,
                custom_until=custom_until,
                custom_seconds=custom_seconds
            )
            self._send_json(res)

        elif path == "/api/accounts/adjust_cooldown":
            account_id = payload.get("account_id")
            new_until = payload.get("new_until")
            reason = payload.get("reason", "Horário de reset ajustado manualmente")
            acc = self.vault.adjust_account_cooldown(account_id, new_until, reason=reason)
            if acc:
                self.vault.log_history("COOLDOWN_ADJUSTED", f"Reset da conta '{acc.get('name')}' ajustado manualmente.")
                self._send_json({"success": True, "message": f"Horário de reset da conta '{acc.get('name')}' atualizado com sucesso!", "account": acc})
            else:
                self._send_json({"success": False, "message": "Data/hora inválida ou já ultrapassada."}, 400)

        elif path == "/api/accounts/set_schedule":
            account_id = payload.get("account_id")
            weekly_reset_day = int(payload.get("weekly_reset_day", 0))
            weekly_reset_time = str(payload.get("weekly_reset_time", "00:00"))
            acc = self.vault.update_account_schedule(account_id, weekly_reset_day, weekly_reset_time)
            if acc:
                self.vault.log_history("SCHEDULE_UPDATED", f"Ciclo de reset semanal configurado para '{acc.get('name')}'.")
                self._send_json({"success": True, "message": "Ciclo de renovação semanal configurado com sucesso!", "account": acc})
            else:
                self._send_json({"success": False, "message": "Conta não encontrada."}, 404)

        elif path == "/api/reset_cooldown":
            account_id = payload.get("account_id")
            acc = self.vault.clear_account_cooldown(account_id)
            if acc:
                self.vault.log_history("COOLDOWN_CLEARED", f"Cooldown resetado manualmente para '{acc.get('name')}'.")
                self._send_json({"success": True, "message": f"Cooldown da conta '{acc.get('name')}' resetado com sucesso!", "account": acc})
            else:
                self._send_json({"success": False, "message": "Conta não encontrada."}, 404)

        elif path == "/api/accounts/capture_active":
            name = payload.get("name", "Conta Capturada")
            email = payload.get("email", "")
            res = self.switcher.capture_active_wincred(name=name, email=email)
            self._send_json(res)

        elif path == "/api/accounts/delete":
            account_id = payload.get("account_id")
            success = self.vault.remove_account(account_id)
            self._send_json({"success": success, "message": "Conta removida do cofre." if success else "Falha ao remover conta."})

        elif path == "/api/accounts/update":
            account_id = payload.get("account_id")
            name = payload.get("name")
            email = payload.get("email")
            acc = self.vault.get_account(account_id)
            if acc:
                self.vault.add_or_update_account(
                    name=name or acc.get("name"),
                    email=email or acc.get("email"),
                    blob=acc.get("blob"),
                    username=acc.get("username", "antigravity"),
                    account_id=account_id
                )
                self._send_json({"success": True, "message": "Conta atualizada com sucesso!"})
            else:
                self._send_json({"success": False, "message": "Conta não encontrada."}, 404)

        elif path == "/api/accounts/enroll":
            name = payload.get("name", "Nova Conta Google")
            email = payload.get("email", "")

            # Executa captura em thread para não travar resposta se for assíncrono
            def run_enroll():
                enroll_new_google_account(account_name=name, account_email=email, restore_previous=False)

            t = threading.Thread(target=run_enroll, daemon=True)
            t.start()
            self._send_json({"success": True, "message": "Janela de login iniciada! Conclua o login na janela que se abriu."})

        elif path == "/api/auto_detect":
            events = self.detector.scan_proxy_rate_limits()
            if events:
                self._send_json({"success": True, "events": events, "message": f"{len(events)} limite(s) detectado(s) e sincronizado(s) com sucesso!"})
            else:
                self._send_json({"success": True, "events": [], "message": "Nenhum novo bloqueio de cota detectado. Todas as contas saudáveis."})

        elif path == "/api/parse_error":
            text = payload.get("text", "")
            account_id = payload.get("account_id")
            info = parse_error_output(text)
            if info:
                res = self.switcher.report_quota_and_swap(
                    quota_type=info.get("type", "5h"),
                    account_id=account_id,
                    custom_until=info.get("cooldown_until")
                )
                res["detected"] = info
                res["message"] = f"Auto-detectado ({info.get('detected_pattern')}): {res.get('message')}"
                self._send_json(res)
            else:
                self._send_json({"success": False, "message": "Nenhum padrão de reset reconhecido no texto informado."}, 400)

        elif path == "/api/sync_proxy":
            # Sincroniza com ~/.config/antigravity-proxy/accounts.json se o script existir
            sync_script = BASE_DIR.parent / ".agents" / "skills" / "cli-orchestrator" / "scripts" / "sync_agy_to_proxy.py"
            if sync_script.exists():
                proc = subprocess.run([sys.executable, str(sync_script)], cwd=str(BASE_DIR.parent), capture_output=True, text=True)
                self._send_json({"success": proc.returncode == 0, "message": "Contas sincronizadas com o proxy de IA!"})
            else:
                self._send_json({"success": True, "message": "Proxy não configurado neste ambiente."})

        else:
            self.send_error(404, "Endpoint não encontrado")

def run(port: int = PORT):
    server_address = ("127.0.0.1", port)
    HotswapHandler.detector.start_background_scanner(interval=4)
    httpd = HTTPServer(server_address, HotswapHandler)
    print("=" * 60)
    print(f"  Gerenciador de Hot-Swap de Contas Antigravity")
    print(f"  Auto-Monitoramento de Cotas Google: ATIVO (segundo plano)")
    print(f"  Acesse: http://127.0.0.1:{port}")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        HotswapHandler.detector.stop_background_scanner()
        print("\nServidor encerrado.")

if __name__ == "__main__":
    run()
