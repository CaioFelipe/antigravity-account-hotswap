import os
import sys
import json
import base64
import socket
import datetime
import threading
import webbrowser
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, Optional
from pathlib import Path

from .vault import AccountsVault, _extract_email_from_blob

from .oauth_config import get_google_oauth_client

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/aicode",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs"
]

SUCCESS_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <title>Login Concluído!</title>
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0a0d14;
      color: #f8fafc;
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100vh;
      margin: 0;
    }
    .card {
      background: #121722;
      padding: 36px 48px;
      border-radius: 14px;
      border: 1px solid #27334d;
      text-align: center;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.6);
      max-width: 440px;
    }
    .icon {
      font-size: 3rem;
      margin-bottom: 12px;
    }
    h2 {
      color: #10b981;
      font-size: 1.4rem;
      margin-bottom: 10px;
    }
    p {
      color: #94a3b8;
      font-size: 0.95rem;
      line-height: 1.5;
      margin-bottom: 8px;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">⏳</div>
    <h2>Autorização recebida, finalizando...</h2>
    <p>O painel Hot-Swap está validando sua credencial junto ao Google agora.</p>
    <p style="font-size: 0.84rem; color: #64748b;">Volte ao painel do Hot-Swap: se a conta não aparecer em alguns segundos, use o botão "Sincronizar do Windows" (Plano B) para garantir o cadastro.</p>
  </div>
  <script>
    setTimeout(() => { window.close(); }, 3000);
  </script>
</body>
</html>
"""

class OAuthCallbackServer(HTTPServer):
    def __init__(self, server_address, RequestHandlerClass, manager):
        super().__init__(server_address, RequestHandlerClass)
        self.manager = manager

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/auth/callback":
            query = urllib.parse.parse_qs(parsed.query)
            code = query.get("code", [None])[0]
            error = query.get("error", [None])[0]

            if code:
                # Retorna página de sucesso no navegador imediatamente
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                html_bytes = SUCCESS_HTML.encode("utf-8")
                self.send_header("Content-Length", str(len(html_bytes)))
                self.end_headers()
                self.wfile.write(html_bytes)

                # Processa os tokens em background
                threading.Thread(target=self.server.manager._exchange_code_for_tokens, args=(code,), daemon=True).start()
            else:
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                err_msg = error or "Nenhum código de autorização fornecido."
                err_body = f"<h2>Falha na Autorização</h2><p>{err_msg}</p>".encode("utf-8")
                self.send_header("Content-Length", str(len(err_body)))
                self.end_headers()
                self.wfile.write(err_body)
                self.server.manager._set_error(f"Erro no Google OAuth: {err_msg}")
        else:
            self.send_response(404)
            self.end_headers()

class OAuthEnrollmentManager:
    """
    Gerenciador singleton de cadastro automático de contas Google.
    Abre o navegador automaticamente e captura o redirecionamento local sem intervenção manual.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_state()
            return cls._instance

    def _init_state(self):
        self.state = "idle"  # idle | waiting_login | completed | error
        self.auth_url = ""
        self.account_name = ""
        self.error_message = ""
        self.account_data = None
        self.server: Optional[OAuthCallbackServer] = None
        self.server_thread: Optional[threading.Thread] = None
        self.redirect_port = 0

    def start_enrollment(self, account_name: str = "Nova Conta Google") -> Dict[str, Any]:
        with self._lock:
            self._stop_server()

            self.account_name = account_name
            self.state = "waiting_login"
            self.error_message = ""
            self.account_data = None

            try:
                # Inicia servidor HTTP local em porta efêmera livre
                self.server = OAuthCallbackServer(("127.0.0.1", 0), OAuthCallbackHandler, self)
                self.redirect_port = self.server.server_port
                redirect_uri = f"http://localhost:{self.redirect_port}/auth/callback"

                client_id, _ = get_google_oauth_client()
                if not client_id:
                    self.state = "error"
                    self.error_message = "Credenciais do Google OAuth não localizadas no sistema."
                    return {"success": False, "message": self.error_message}

                params = {
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "scope": " ".join(SCOPES),
                    "access_type": "offline",
                    "prompt": "consent"
                }
                self.auth_url = f"{GOOGLE_AUTH_URL}?" + urllib.parse.urlencode(params)

                # Inicia loop do servidor em thread separada
                self.server_thread = threading.Thread(target=self._run_server, daemon=True)
                self.server_thread.start()

                # Abre o navegador automaticamente
                try:
                    webbrowser.open(self.auth_url)
                except Exception:
                    pass

                return {
                    "success": True,
                    "state": "waiting_login",
                    "auth_url": self.auth_url,
                    "message": "Navegador aberto na tela de login da Google. Selecione sua conta e autorize."
                }
            except Exception as e:
                self.state = "error"
                self.error_message = f"Falha ao iniciar servidor de login: {str(e)}"
                return {"success": False, "message": self.error_message}

    def _run_server(self):
        if self.server:
            try:
                self.server.serve_forever()
            except Exception:
                pass

    def _stop_server(self):
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
            self.server = None
            self.server_thread = None

    def _exchange_code_for_tokens(self, code: str):
        redirect_uri = f"http://localhost:{self.redirect_port}/auth/callback"
        try:
            client_id, client_secret = get_google_oauth_client()
            params = urllib.parse.urlencode({
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri
            }).encode("utf-8")

            req = urllib.request.Request(
                GOOGLE_TOKEN_URL,
                data=params,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                token_data = json.loads(resp.read().decode("utf-8"))

            now_dt = datetime.datetime.now(datetime.timezone.utc)
            expires_in = token_data.get("expires_in", 3600)
            expiry_dt = now_dt + datetime.timedelta(seconds=expires_in)
            expiry_str = expiry_dt.astimezone().isoformat()

            id_token = token_data.get("id_token", "")
            refresh_token = token_data.get("refresh_token", "")

            # Extrai e-mail e nome do usuário do id_token (JWT)
            email = "nova_conta@google.com"
            display_name = self.account_name or "Conta Google"
            if id_token and "." in id_token:
                try:
                    parts = id_token.split(".")
                    payload_b64 = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
                    jwt_payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8"))
                    email = jwt_payload.get("email", email)
                    if not self.account_name or self.account_name == "Nova Conta Google":
                        display_name = jwt_payload.get("name", f"Conta ({email})")
                except Exception:
                    pass

            new_blob_dict = {
                "token": {
                    "access_token": token_data.get("access_token"),
                    "token_type": token_data.get("token_type", "Bearer"),
                    "refresh_token": refresh_token,
                    "expiry": expiry_str
                },
                "auth_method": "consumer",
                "id_token": id_token
            }
            blob_str = json.dumps(new_blob_dict)

            # Salva no cofre de contas
            vault = AccountsVault()
            account = vault.add_or_update_account(
                name=display_name,
                email=email,
                blob=blob_str,
                username="antigravity"
            )

            vault.log_history("ACCOUNT_ENROLLED", f"Nova conta '{display_name}' ({email}) cadastrada com sucesso!")

            self.state = "completed"
            self.account_data = account

        except Exception as e:
            self._set_error(f"Falha ao validar token junto ao Google: {str(e)}")
        finally:
            # Encerra o servidor de callback local
            self._stop_server()

    def _set_error(self, err_msg: str):
        self.state = "error"
        self.error_message = err_msg
        self._stop_server()

    def cancel_enrollment(self) -> Dict[str, Any]:
        with self._lock:
            self._stop_server()
            self.state = "idle"
            self.auth_url = ""
            return {"success": True, "message": "Cadastro cancelado."}

    def get_status(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "auth_url": self.auth_url,
            "account_name": self.account_name,
            "error_message": self.error_message,
            "account": self.account_data
        }

# Instância global
enrollment_manager = OAuthEnrollmentManager()

def enroll_new_google_account(account_name: str, account_email: str = "", restore_previous: bool = False) -> Dict[str, Any]:
    return enrollment_manager.start_enrollment(account_name)
