# 🔄 Antigravity Account Hot-Swap & Quota Manager

Sistema autônomo, limpo e de alta performance para gerenciamento de múltiplas contas do Google no **Google Antigravity / agy CLI**, alternância atômica (*Hot-Swap*) no **Windows Credential Manager (WinCred)** e **monitoramento de cotas em tempo real** (janela de 5 horas e limite semanal) com sincronização e reinício em 1 clique.

---

## 🔒 Segurança e Privacidade por Design

* **Zero Credenciais Incluídas:** Este repositório de código aberto **não contém nenhuma chave de API, token OAuth ou credencial pessoal**.
* Todas as contas e tokens adicionados pelo usuário ficam armazenados exclusivamente de forma local na máquina dele (ignorado pelo `.gitignore` em `data/accounts_vault.json`).
* As credenciais do Google OAuth Client são descobertas dinamicamente a partir da instalação local do Antigravity, dispensando segredos estáticos no código.
* As substituições de token acontecem diretamente no Cofre de Credenciais do Windows (`advapi32.dll:CredWriteW`), sem expor senhas em texto puro.

---

## ⚡ Principais Recursos

1. **Monitoramento de Cotas em Tempo Real:**
   - Consulta diretamente a telemetria do Antigravity (`language_server.exe`) para exibir a porcentagem exata disponível e o tempo restante para o reset da janela de 5 horas e cota semanal.
   - Suporte a modelos como Gemini 3.1 Pro, Gemini 3.8 Flash e Claude Sonnet 4.6.
2. **Hot-Swap com 1 Clique:**
   - Alterne instantaneamente entre contas Google cadastradas.
   - O sistema renova os tokens `access_token` e `id_token` junto ao Google e atualiza o Windows Credential Manager.
3. **Reinício e Sincronização em 1 Clique:**
   - Botão **🔄 Reiniciar Antigravity (1-Clique)** no painel Web e na janela desktop, além do script `recarregar_antigravity.bat`.
   - Fecha e reabre o Antigravity em ~2 segundos com a nova conta já autenticada e as sessões restauradas.
4. **Cadastro 100% Automático de Contas Google:**
   - Abre o navegador automaticamente na página oficial de consentimento do Google.
   - Captura automática do redirecionamento via servidor local — **sem necessidade de copiar e colar códigos manualmente**.
5. **Interface Minimalista e Direta:**
   - Sem poluição visual: cards das contas, barras de consumo em tempo real e botão de troca rápida.
   - Disponível em **Painel Web Localhost** (porta `5055`) e **Janela Desktop (Tkinter)**.

---

## 🚀 Como Iniciar

### 1. Painel Web Localhost (Recomendado)
Execute o arquivo:
```powershell
.\iniciar_painel_web.bat
```
Ou via terminal:
```powershell
python web/server.py
```
Acesse no seu navegador: **`http://localhost:5055`**

### 2. Janela Desktop Nativa (Tkinter)
Execute o arquivo:
```powershell
.\iniciar_janela_tkinter.bat
```
Ou via terminal:
```powershell
python gui_tkinter.py
```

### 3. Reiniciar Antigravity Rapidamente
Dê dois cliques no arquivo:
```powershell
.\recarregar_antigravity.bat
```
(ou `.\reiniciar_antigravity.bat`)

### 4. Linha de Comando (CLI)
```powershell
# Listar contas e status de cota
python cli.py list

# Fazer Hot-Swap imediato para outra conta
python cli.py switch acc_2

# Trocar automaticamente para a próxima conta saudável
python cli.py auto-swap
```

---

## 📁 Estrutura do Repositório

```text
antigravity-account-hotswap/
├── core/
│   ├── quota_checker.py    # Leitura em tempo real de cotas e reset do Antigravity
│   ├── ide_reloader.py     # Reinício e recarregamento automatizado do Antigravity
│   ├── oauth_config.py     # Descoberta dinâmica e segura do cliente OAuth Google
│   ├── oauth_capture.py    # Fluxo 100% automatizado de login Google via callback local
│   ├── switcher.py         # Motor de Hot-Swap no Windows Credential Manager
│   ├── wincred.py          # Integração nativa com a API Win32 Credential Manager
│   ├── vault.py            # Gestão segura do cofre de contas local
│   └── auto_detector.py    # Auto-detector de telemetria e proxy
├── web/
│   ├── server.py           # Servidor REST HTTP Dual-Stack (porta 5055)
│   └── index.html          # Dashboard minimalista Dark Mode com barras de cota ao vivo
├── data/
│   └── accounts_vault.example.json # Template de banco de contas (limpo)
├── gui_tkinter.py          # Interface gráfica nativa Windows (Tkinter)
├── cli.py                  # Ferramenta para linha de comando
├── iniciar_painel_web.bat  # Atalho de inicialização rápida Web
├── iniciar_janela_tkinter.bat # Atalho de inicialização rápida Desktop
├── recarregar_antigravity.bat # Reinício limpo e rápido do Antigravity
├── reiniciar_antigravity.bat  # Alias para reinício do Antigravity
├── .gitignore              # Proteção contra commit de credenciais
└── README.md               # Documentação do projeto
```
