# 🎥 Controle e Gravação de Câmeras - Farmácia (v3.6 NVR Unificado)

Este projeto é uma solução de NVR (Network Video Recorder) de baixíssimo consumo de hardware, projetada para capturar, gravar, monitorar e gerenciar múltiplas câmeras inteligentes (compatíveis com o ecossistema Tuya/Positivo) em um único arquivo unificado, com portabilidade total, prevenção de duplicidade na rede e auto-atualização direta via GitHub.

---

## 🛠️ Como Funciona o Sistema

O sistema é 100% integrado em um único arquivo gerenciador (`gerenciador.pyw`) e uma ponte RTSP:

1. **Ponte RTSP (`go2rtc.exe`)**:
   Estabelece conexões seguras e autenticadas com os servidores Tuya Cloud e expõe as transmissões de vídeo das câmeras na rede local (nos formatos RTSP na porta `8554` e API Web na porta `1984`).
   
2. **NVR Unificado integrado no Gerenciador (`gerenciador.pyw`)**:
   - **0% Transcoding (Cópia Direta)**: Executado em threads de segundo plano (daemon threads), o gerenciador se conecta à API HTTP do `go2rtc` (`/api/stream.mp4?src=NOME`) e baixa diretamente o fluxo binário H.264/H.265 do vídeo, salvando-o no disco. Isso elimina todo o processamento de decodificação e recodificação de vídeo. O uso de CPU é de **0%**, e os arquivos finais ocupam o mínimo de espaço na qualidade original.
   - **Modo Headless/Silencioso**: Suporta a inicialização em segundo plano via linha de comando (`gerenciador.pyw --silent`) usada pelo script de inicialização do Windows (`.vbs`), rodando silenciosamente sem abrir janelas.
   - **Contingência de Sincronização**: Se a pasta do Google Drive (`G:\Meu Drive\CAMERAS`) estiver offline, sem espaço ou sem permissão de escrita, o NVR desvia a gravação automaticamente para a pasta local `backup_gravacoes/`.
   - **Sincronizador Automático**: Uma thread dedicada monitora o Google Drive e, assim que a conexão é restabelecida, realiza o upload em segundo plano dos vídeos da pasta `backup_gravacoes/`, deletando a cópia local para não encher o HD.
   - **Detecção de Duplicidade na Rede**: Envia batimentos cardíacos (heartbeat) a cada 30 segundos para o Google Drive em formato JSON (`.active_recorder_{stream}.json`). Se outra máquina tentar gravar a mesma câmera no mesmo diretório, o conflito é detectado e o segundo gravador cessa a gravação imediatamente para evitar corrupção e duplicidade.
   - **Monitor de Visualizadores**: Consulta a API do `go2rtc` e exibe em tempo real o IP e o navegador das pessoas que estão assistindo à live das câmeras no painel Web.

3. **Atualização Automática Inteligente**:
   Ao abrir a interface do gerenciador de forma visual, o sistema consulta a versão mais recente dos códigos `gerenciador.pyw` e `visualizador.html` diretamente no repositório GitHub. Se houver uma nova versão, o gerenciador avisa o usuário, encerra os serviços com segurança, baixa os arquivos novos, sobrescreve o próprio código e o HTML (mantendo as credenciais intactas em `go2rtc.yaml`), e reinicia automaticamente na nova versão.

---

## 📂 Estrutura de Arquivos do Projeto

```
📁 camera farmacia/
├── 📁 go2rtc/
│   ├── 📄 go2rtc.exe                   # Ponte RTSP nativa go2rtc compilada para Windows
│   ├── 📄 go2rtc.yaml                  # Configurações de credenciais e IDs das câmeras
│   └── 📄 go2rtc_start.log             # Logs da ponte go2rtc
├── 📁 backup_gravacoes/                # Diretório automático para vídeos salvos localmente em contingência
├── 📄 gerenciador.pyw                  # Interface Gráfica de Controle & NVR Unificado (Pythonw)
├── 📄 visualizador.html                # Painel de Visualização Web responsivo profissional
├── 📄 Liberar Rede Local (Executar como Admin).bat   # Script para abrir as portas de rede no Firewall
├── 📄 README.md                        # Documentação Geral do Sistema
└── 📄 .gitignore                       # Configurações de arquivos ignorados pelo Git
```

---

## 💻 Como Configurar e Rodar em Outro Computador

Como o projeto possui caminhos 100% dinâmicos e relativos, a portabilidade é imediata:

### Passo 1: Instalar o Python
Baixe e instale o Python (recomendado 3.10 ou superior) no site oficial.
> ⚠️ **IMPORTANTE**: Durante a instalação, marque a caixa **"Add Python to PATH"** (Adicionar Python às variáveis de ambiente).

### Passo 2: Clonar ou Copiar o Repositório
Copie a pasta inteira do projeto (ou clone o repositório git) para qualquer pasta do novo computador.

### Passo 3: Configurar as Câmeras em `go2rtc.yaml`
Abra o arquivo `go2rtc/go2rtc.yaml` com o Bloco de Notas e configure as credenciais das suas câmeras Tuya/Positivo. O painel gráfico detectará os novos nomes e quantidades automaticamente.
```yaml
streams:
  camera1: "tuya://protect-us.ismartlife.me?device_id=ID_AQUI&email=SEU_EMAIL&password=SUA_SENHA"
  camera2: "tuya://protect-us.ismartlife.me?device_id=OUTRO_ID&email=SEU_EMAIL&password=SUA_SENHA"
```

### Passo 4: Executar e Ativar Inicialização Automática
1. Dê um duplo clique no arquivo `gerenciador.pyw` para abrir a interface gráfica.
2. O gerenciador irá buscar por atualizações no GitHub e se atualizar automaticamente se houver nova versão.
3. Clique no botão **"⚙️ Habilitar Inicialização Automática com o Windows"**. 
4. Pronto! O sistema gerará o arquivo `.vbs` na pasta de Inicialização do Windows com os caminhos corretos deste PC, iniciando o gravador de forma silenciosa toda vez que o Windows ligar.

---

## 🤖 Guia de Orientação para Assistentes de IA (Claude, Antigravity, Cursor, etc.)

Se você é uma Inteligência Artificial atuando neste repositório para manutenção, melhoria ou suporte, leia as regras de negócio abaixo para evitar regressões de código:

### ⚙️ Regras Arquiteturais
1. **Gravação Sem OpenCV/NumPy**: O fluxo é capturado no formato bruto H.264/H.265 HTTP do `go2rtc` diretamente pela biblioteca nativa `urllib.request`. Nunca adicione decodificação de imagem/frames com OpenCV ou processamento com NumPy, pois isso consome CPU e memória.
2. **Caminhos Relativos**: Nunca use caminhos absolutos como `C:\Users\Thiesen\...` nos códigos. Sempre utilize `os.path.dirname(os.path.abspath(__file__))` para obter o caminho relativo à pasta do projeto.
3. **Mapeamento de Pastas do Drive**:
   - `farmacia` -> `CAMERA 1 FARMACIA`
   - `farmacia2` -> `CAMERA 2 FARMACIA`
   - Câmeras adicionais de index $i \ge 2$ -> `CAMERA {i+1} {NOME_STREAM}`
4. **Arquivo de Trava e Encerramento Gracioso**:
   - Os processos do gravador em execução são identificados pelo arquivo `.lock` com o PID.
   - O painel (`gerenciador.pyw`) monitora os processos de forma nativa consultando a API do Windows via `ctypes` (`kernel32.OpenProcess` e `GetExitCodeProcess`) para economizar processamento e não travar o loop do Tkinter.
5. **Preservar Credenciais**: O sistema de atualização baixa os arquivos do GitHub, mas NUNCA deve alterar ou baixar `go2rtc.yaml`, mantendo as senhas das câmeras configuradas no computador local intactas.