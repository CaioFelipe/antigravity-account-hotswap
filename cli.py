import sys
import time
import argparse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from core.vault import AccountsVault, WEEKDAY_NAMES
from core.switcher import AccountSwitcher
from core.oauth_capture import enroll_new_google_account
from core.auto_detector import AutoQuotaDetector, parse_error_output

def main():
    parser = argparse.ArgumentParser(description="CLI de Gerenciamento de Hot-Swap de Contas Antigravity")
    subparsers = parser.add_subparsers(dest="command", help="Comando a executar")

    # list
    subparsers.add_parser("list", help="Lista todas as contas, status e ciclos de renovação")

    # switch
    p_switch = subparsers.add_parser("switch", help="Realiza Hot-Swap para uma conta específica")
    p_switch.add_argument("account_id", help="ID da conta (ex: acc_1)")

    # auto-swap
    subparsers.add_parser("auto-swap", help="Troca automaticamente para a próxima conta saudável")

    # auto-detect
    subparsers.add_parser("auto-detect", help="Varre telemetria da Google e sincroniza cotas automaticamente")

    # parse-error
    p_pe = subparsers.add_parser("parse-error", help="Analisa texto de erro do Google/CLI e aplica reset automaticamente")
    p_pe.add_argument("error_text", help="Texto ou log da mensagem de erro do Google")
    p_pe.add_argument("--id", dest="account_id", default=None, help="ID da conta (opcional)")

    # exhaust
    p_exhaust = subparsers.add_parser("exhaust", help="Registra cota esgotada e executa Hot-Swap automático com cálculo preciso")
    p_exhaust.add_argument("--type", choices=["5h", "weekly"], default="5h", help="Tipo de cota esgotada (5h ou semanal)")
    p_exhaust.add_argument("--id", dest="account_id", default=None, help="ID da conta (se omitido, usa a ativa)")
    p_exhaust.add_argument("--until", default=None, help="Data/Hora exata de reset (ex: 2026-10-03T14:00 ou 16:30)")
    p_exhaust.add_argument("--hours", type=float, default=None, help="Horas restantes informadas no erro")
    p_exhaust.add_argument("--minutes", type=float, default=None, help="Minutos restantes informados no erro")

    # adjust-cooldown
    p_adj = subparsers.add_parser("adjust-cooldown", help="Ajusta manualmente o horário de reset de uma conta")
    p_adj.add_argument("account_id", help="ID da conta")
    p_adj.add_argument("--until", required=True, help="Data/Hora exata de retorno (ex: 2026-10-03T09:00 ou 18:00)")

    # set-schedule
    p_sched = subparsers.add_parser("set-schedule", help="Configura o dia da semana e horário que a cota semanal renova")
    p_sched.add_argument("account_id", help="ID da conta")
    p_sched.add_argument("--day", type=int, choices=range(0, 7), required=True, help="Dia da semana (0=Segunda ... 6=Domingo)")
    p_sched.add_argument("--time", default="00:00", help="Horário de renovação (HH:MM, padrão 00:00)")

    # clear-cooldown
    p_clear = subparsers.add_parser("clear-cooldown", help="Libera o cooldown de uma conta")
    p_clear.add_argument("account_id", help="ID da conta")

    # capture
    p_cap = subparsers.add_parser("capture", help="Captura a credencial atualmente ativa no Windows")
    p_cap.add_argument("--name", default="Conta Antigravity Capturada", help="Nome/identificador da conta")

    # enroll
    p_enroll = subparsers.add_parser("enroll", help="Inicia o fluxo para cadastrar nova conta Google")
    p_enroll.add_argument("--name", required=True, help="Nome/identificador da nova conta")
    p_enroll.add_argument("--email", default="", help="E-mail da nova conta")

    # daemon
    p_daemon = subparsers.add_parser("daemon", help="Executa monitor contínuo com auto-detecção em segundo plano")
    p_daemon.add_argument("--interval", type=int, default=5, help="Intervalo de checagem em segundos")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    vault = AccountsVault()
    switcher = AccountSwitcher(vault=vault)
    detector = AutoQuotaDetector(vault=vault, switcher=switcher)

    if args.command == "list":
        status = switcher.get_status()
        active = status.get("active_account")
        now = status.get("now", time.time())
        print("\n" + "=" * 80)
        print("  CONTAS REGISTRADAS NO COFRE DE HOT-SWAP (ANTIGRAVITY)")
        print("=" * 80)
        print(f"Conta Ativa no Antigravity: {active.get('name') if active else 'Nenhuma'} ({active.get('email') if active else '-'})")
        print("-" * 80)
        print(f"{'ID':<8} {'Nome':<20} {'E-mail':<24} {'Status':<14} {'Ciclo Semanal':<14}")
        print("-" * 80)
        for acc in status.get("accounts", []):
            is_active = active and active.get("id") == acc.get("id")
            st = "ATIVA" if is_active else acc.get("status")
            w_day = acc.get("weekly_reset_day", 0)
            w_time = acc.get("weekly_reset_time", "00:00")
            day_name = WEEKDAY_NAMES[w_day][:3] if 0 <= w_day < 7 else "Seg"
            cycle_str = f"{day_name} {w_time}"

            if acc.get("cooldown_until"):
                diff = max(0, int(acc.get("cooldown_until") - now))
                h = diff // 3600
                m = (diff % 3600) // 60
                st += f" ({h}h{m}m)"

            print(f"{acc.get('id'):<8} {acc.get('name')[:18]:<20} {acc.get('email')[:22]:<24} {st:<14} {cycle_str:<14}")
        print("=" * 80 + "\n")

    elif args.command == "switch":
        res = switcher.switch_to_account(args.account_id)
        print(res.get("message"))

    elif args.command == "auto-swap":
        res = switcher.report_quota_and_swap(quota_type="5h")
        print(res.get("message"))

    elif args.command == "auto-detect":
        events = detector.scan_proxy_rate_limits()
        if events:
            print(f"[OK] {len(events)} evento(s) de limite auto-detectado(s) e sincronizado(s):")
            for ev in events:
                print(f"  -> Conta {ev['email']} | Modelo: {ev['model']} | Restam ~{ev['minutes_remaining']} min")
        else:
            print("[OK] Nenhuma conta com limite pendente na telemetria local. Todas saudáveis!")

    elif args.command == "parse-error":
        info = parse_error_output(args.error_text)
        if info:
            print(f"[PADRÃO DETECTADO]: {info.get('detected_pattern')}")
            res = switcher.report_quota_and_swap(
                quota_type=info.get("type", "5h"),
                account_id=args.account_id,
                custom_until=info.get("cooldown_until")
            )
            print(res.get("message"))
        else:
            print("[AVISO] Nenhum padrão de tempo reconhecido na mensagem fornecida.")

    elif args.command == "exhaust":
        custom_sec = None
        if args.hours:
            custom_sec = int(args.hours * 3600)
        elif args.minutes:
            custom_sec = int(args.minutes * 60)

        res = switcher.report_quota_and_swap(
            quota_type=args.type,
            account_id=args.account_id,
            custom_until=args.until,
            custom_seconds=custom_sec
        )
        print(res.get("message"))

    elif args.command == "adjust-cooldown":
        acc = vault.adjust_account_cooldown(args.account_id, args.until)
        if acc:
            print(f"Horário de reset da conta '{acc.get('name')}' ajustado para: {args.until}")
        else:
            print("Erro: Data/hora inválida ou já ultrapassada.")

    elif args.command == "set-schedule":
        acc = vault.update_account_schedule(args.account_id, args.day, args.time)
        if acc:
            day_name = WEEKDAY_NAMES[args.day]
            print(f"Ciclo semanal da conta '{acc.get('name')}' configurado para: Toda {day_name} às {args.time}")
        else:
            print("Conta não encontrada.")

    elif args.command == "clear-cooldown":
        acc = vault.clear_account_cooldown(args.account_id)
        if acc:
            print(f"Cooldown da conta '{acc.get('name')}' liberado com sucesso!")
        else:
            print("Conta não encontrada.")

    elif args.command == "capture":
        res = switcher.capture_active_wincred(name=args.name)
        print(res.get("message"))

    elif args.command == "enroll":
        print(f"Iniciando login para '{args.name}'...")
        res = enroll_new_google_account(account_name=args.name, account_email=args.email)
        print(res.get("message"))

    elif args.command == "daemon":
        print(f"Iniciando daemon autônomo com auto-detecção da Google (a cada {args.interval}s)...")
        print("Pressione Ctrl+C para encerrar.")
        try:
            while True:
                detector.scan_proxy_rate_limits()
                vault.refresh_cooldowns()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nDaemon finalizado.")

if __name__ == "__main__":
    main()
