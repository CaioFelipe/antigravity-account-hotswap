# 🔄 Antigravity Account Hot-Swap & Quota Manager

Sistema autônomo, limpo e de alta performance para gerenciamento de múltiplas contas do Google no **Google Antigravity / agy CLI**, alternância atômica (*Hot-Swap*) no **Windows Credential Manager (WinCred)** e **monitoramento de cotas em tempo real** (janela de 5 horas e limite semanal) com sincronização e reinício em 1 clique.

---

## 🔒 Segurança e Privacidade por Design

* **Zero Credenciais Incluídas:** Este repositório de código aberto **não contém nenhuma chave de API, token OAuth ou credencial pessoal**.
* Todas as contas e tokens adicionados pelo usuário ficam armazenados exclusivamente de forma local na máquina dele (ignorado pelo `.gitignore` em `data/*.json`).
* As credenciais do Google OAuth Client são descobertas dinamicamente a partir da instalação local do Antigravity, dispensando segredos estáticos no código.
* As substituições de token acontecem diretamente no Cofre de Credenciais do Windows (`advapi32.dll:CredWriteW`), sem expor senhas em texto puro.

---

## ⚡ Principais Recursos

1. **Monitoramento de Cotas em Tempo Real:**
   - Consulta diretamente a telemetria do Antigravity (`language_server.exe`) para exibir a porcentagem exata disponível e o tempo restante para o reset da janela de 5 horas e cota semanal.
   - Suporte a modelos como Gemini 3.1 Pro, Gemini 3.8 Flash e Claude Sonnet 4.6.
   - A última leitura real de cada conta é persistida no cofre, para que uma conta inativa continue mostrando seu percentual verdadeiro em vez de aparecer como "100% livre" por padrão.
2. **Auto-Swap por Cota Crítica (2%):**
   - Uma varredura em segundo plano monitora a cota ao vivo da conta ativa. Se a cota disponível (5h ou semanal) cair a **2% ou menos**, o sistema troca automaticamente para a próxima conta saudável, usando o horário exato de reset informado pelo Google.
3. **Hot-Swap com 1 Clique (manual ou automático):**
   - Alterne instantaneamente entre contas Google cadastradas, manualmente pelo painel ou automaticamente pelo monitor de cota.
   - O sistema renova os tokens `access_token` e `id_token` junto ao Google e atualiza o Windows Credential Manager.
   - Após a troca, tira um snapshot de todas as conversas abertas (ocupadas ou ociosas) e as retoma automaticamente depois do reinício — incluindo navegar de volta para a URL exata de cada conversa, caso o Antigravity reabra na tela inicial.
4. **Cadastro de Contas Google:**
   - Fluxo automático: abre o navegador na página de login do Google e captura o retorno via servidor local. Pode falhar dependendo da instalação local (ver nota abaixo).
   - Plano B sempre confiável: faça login da conta direto no Antigravity e use "Sincronizar do Windows" no painel para capturá-la.
5. **Interface Única:**
   - Um único painel: **Web Localhost** (porta `5055`). Sem Tkinter, sem duplicação de scripts.

---

## 🚀 Como Iniciar

### Painel Web Localhost (única interface)
Execute o arquivo:
```powershell
.\iniciar_painel_web.bat
```
Ou via terminal:
```powershell
python web/server.py
```
Acesse no seu navegador: **`http://localhost:5055`**

Reiniciar/sincronizar o Antigravity e trocar de conta são feitos pelos botões do próprio painel — eles acionam o snapshot e a retomada automática das conversas, o que os antigos scripts `.bat` de restart não faziam.

### Linha de Comando (CLI)
```powershell
# Listar contas e status de cota
python cli.py list

# Fazer Hot-Swap imediato para outra conta
python cli.py switch acc_2

# Trocar automaticamente para a próxima conta saudável
python cli.py auto-swap
```

---

## ⚠️ Nota sobre o cadastro automático de contas

O fluxo de login automático (botão "Abrir Login no Navegador") depende de um `client_secret` OAuth extraído dinamicamente do binário `agy.exe` instalado localmente. Em algumas instalações essa extração pode capturar um valor incorreto, fazendo a troca do código de autorização por tokens falhar silenciosamente — o navegador mostra que a autorização foi recebida, mas a conta não é salva.

Se isso acontecer, use o **Plano B** (sempre funciona, pois reaproveita o login nativo do próprio Antigravity, que já tem as credenciais corretas):
1. Dentro do Antigravity, faça logout e entre com a outra conta Google.
2. No painel Hot-Swap, clique em **"Sincronizar do Windows"** para capturar essa credencial e adicioná-la ao cofre.

---

## 📁 Estrutura do Repositório

```text
antigravity-account-hotswap/
├── core/
│   ├── quota_checker.py    # Leitura em tempo real de cotas, projeção da última leitura conhecida
│   ├── ide_reloader.py     # Reinício/reload do Antigravity, navegação CDP e injeção de 'continue'
│   ├── oauth_config.py     # Descoberta dinâmica e segura do cliente OAuth Google
│   ├── oauth_capture.py    # Fluxo automatizado de login Google via callback local
│   ├── switcher.py         # Motor de Hot-Swap no Windows Credential Manager
│   ├── wincred.py          # Integração nativa com a API Win32 Credential Manager
│   ├── vault.py            # Gestão segura do cofre de contas local
│   ├── chat_tracker.py     # Rastreio de sessões de chat abertas (busy/idle) via CDP
│   └── auto_detector.py    # Monitor de cota crítica (2%) e auto-swap em segundo plano
├── web/
│   ├── server.py           # Servidor REST HTTP Dual-Stack (porta 5055)
│   └── index.html          # Painel único: cards de conta, cotas ao vivo, troca e cadastro
├── data/
│   └── accounts_vault.example.json # Template de banco de contas (limpo)
├── cli.py                  # Ferramenta para linha de comando
├── iniciar_painel_web.bat  # Atalho de inicialização do painel web
├── .gitignore              # Proteção contra commit de credenciais e dados locais
└── README.md               # Documentação do projeto
```
