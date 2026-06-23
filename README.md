# 🎥 Controle e Gravação da Câmeras - Farmácia (v3.0 Portabilidade & Contingência Premium)

Este projeto foi desenvolvido para capturar o sinal de vídeo de duas **Câmeras Inteligentes Positivo (ecossistema Tuya)** na rede local, redimensionar as imagens para salvar espaço e gravar continuamente em blocos exatos de 30 minutos sincronizados com o relógio diretamente em pastas do Google Drive (`G:\Meu Drive\CAMERAS`).

---

## 🛠️ Como Funciona o Sistema

O sistema é composto por três componentes principais trabalhando juntos:

1. **Ponte RTSP (`go2rtc.exe`)**:
   As câmeras Positivo/Tuya possuem portas locais de transmissão bloqueadas de fábrica. O `go2rtc` estabelece uma conexão autenticada com os servidores em nuvem da Tuya e cria transmissões RTSP locais em `rtsp://localhost:8554/farmacia` e `rtsp://localhost:8554/farmacia2`.
   
2. **Script de Gravação (`gravador_camera.py`)**:
   Lê a transmissão local do `go2rtc`, redimensiona os quadros para **HD (1280x720)** para otimizar espaço de armazenamento e salva os arquivos com nomes no formato `camera_AAAA-MM-DD_HH-MM_ate_HH-MM.mp4`. Os arquivos são finalizados e salvos nos minutos `:00` e `:30` do relógio.
   **Contingência Integrada**: Se o Google Drive estiver desconectado, sem espaço ou com erro de permissão de escrita, o script automaticamente desvia a gravação para a pasta local `backup_gravacoes/` no disco local (C:), garantindo que as imagens nunca sejam perdidas.

3. **Interface Gráfica (`gerenciador.pyw`)**:
   Uma interface amigável escrita em Python (Tkinter) com tema escuro que permite ao usuário iniciar, parar, monitorar o status do sistema (com LEDs de status virtuais), consultar o IP local, gerar relatórios de diagnóstico e configurar a inicialização do Windows com um clique.

---

## 💻 Como Instalar e Rodar em Outra Máquina (Configuração)

Caso queira mover este projeto para outro computador, siga os passos abaixo:

### Passo 1: Instalar o Python
Baixe e instale o Python 3.10 ou superior.
> ⚠️ **IMPORTANTE**: Na tela de instalação, certifique-se de marcar a caixa **"Add Python to PATH"** (Adicionar Python às variáveis de ambiente) antes de prosseguir.

### Passo 2: Instalar as Dependências
Abra o Prompt de Comando (CMD) ou PowerShell e instale as bibliotecas necessárias com o comando:
```bash
pip install opencv-python numpy
```

### Passo 3: Baixar/Copiar a Pasta do Projeto
Copie a pasta inteira `camera farmacia` para o novo computador (recomenda-se colocar na Área de Trabalho: `C:\Users\<NomeUsuario>\Desktop\camera farmacia`).

### Passo 4: Configurar as Credenciais da Câmera
Se a conta da Tuya ou a câmera mudar:
1. Abra o arquivo de configuração `go2rtc/go2rtc.yaml` em um editor de texto (como o Bloco de Notas).
2. Atualize as credenciais na linha do stream:
   ```yaml
   streams:
     farmacia: "tuya://protect-us.ismartlife.me?device_id=SEU_DEVICE_ID&email=SEU_EMAIL&password=SUA_SENHA"
   ```
   *Nota: O usuário deve estar cadastrado no aplicativo **Tuya Smart** (não no app da Positivo) com uma senha definida.*

### Passo 5: Inicialização Automática com o Windows (Super Simples)
Para que a gravação inicie de forma invisível em segundo plano toda vez que o Windows ligar:
1. Abra a interface gráfica do projeto executando `gerenciador.pyw`.
2. Clique no botão **"Habilitar Inicialização Automática com o Windows"**.
3. O programa gerará e configurará o script `.vbs` de inicialização de forma dinâmica na pasta correspondente do seu novo PC. Não é necessário editar nenhum arquivo manualmente!

### Passo 6: Acessar a Transmissão de Outros Dispositivos (Opcional)
Se você deseja assistir às câmeras a partir de outros PCs na mesma rede:
1. No PC que está gravando, abra a pasta do projeto.
2. Clique com o botão direito sobre o arquivo `Liberar Rede Local (Executar como Admin).bat` e selecione **"Executar como Administrador"**.
3. O script irá abrir as portas `1984` e `8554` no Firewall do Windows.
4. Agora, em qualquer outro aparelho (PC, celular, tablet) conectado no mesmo Wi-Fi, acesse o link do navegador indicado na interface (Ex: `http://192.168.7.12:1984` ou use o visualizador local `visualizador.html`).

---

## 📈 Histórico de Alterações (Change Log)

### [v1.0] - Versão Inicial (Terminal)
- Implementação do script `gravador_camera.py` com alinhamento de minutos.
- Configuração do `go2rtc` com credenciais Tuya.
- Criação de scripts `.bat` avulsos no Desktop para Iniciar, Parar e Verificar Status via linha de comando.
- Criação do script de inicialização automática `iniciar_gravacao_farmacia.vbs`.

### [v2.0] - Interface Gráfica e Organização
- Criação da pasta centralizada `camera farmacia` no Desktop.
- Criação do gerenciador visual `gerenciador.pyw` com tema escuro básico, eliminando a necessidade de vários scripts `.bat` poluindo a Área de Trabalho.
- Implementação do gerador de diagnósticos técnico (`diagnostico.txt`) que verifica dependências, permissões de escrita do Drive e acessibilidade de rede.

### [v2.1] - Interface Premium
- **LEDs de Status**: Adicionados indicadores LED circulares animados na interface para visualização clara de processos ativos/inativos.
- **Painel de Endereço IP**: Exibe dinamicamente o IP local do computador na interface para facilitar o acesso de outros dispositivos.
- **Cópia Rápida**: Clique sobre o link local para copiá-lo para a área de transferência do Windows instantaneamente.
- **DPI Scaling**: Adicionada compatibilidade com monitores de alta densidade (4K/FullHD) para textos mais nítidos.
- **Atalho no Desktop**: Criação do atalho unificado **Câmera Farmácia** que abre a interface com um clique.

### [v2.2] - Otimizações do Backend
- **Desligamento Seguro (Sem Corrupção)**: Implementado encerramento gracioso via arquivo de trava `gravando.lock` para permitir que o OpenCV finalize a indexação do MP4 antes de fechar.
- **Detecção de Congelamento**: Inclusão de verificação de quadros repetidos. Se a imagem congelar por mais de 15 segundos, o backend força a reconexão automática da ponte RTSP.
- **Timeouts do OpenCV/FFmpeg**: Configurados limites de 5 segundos para conexão e leitura de pacotes, evitando que o script trave indefinidamente em quedas de rede.
- **Relatório de Erros Continuo**: Todos os eventos e erros de sinal do gravador são salvos em `erros_gravador.log` na pasta do projeto.

### [v2.5] - Suporte Duplo de Câmeras
- **Sistema Duplo Unificado**: Expansão da interface para gerenciar a **Câmera 1** e a **Câmera 2** simultaneamente em um painel unificado.
- **Parametrização por CLI**: O script `gravador_camera.py` agora aceita argumentos (`--stream`, `--dir`, `--lock`, `--log`), permitindo rodar duas instâncias independentes a partir do mesmo arquivo.
- **Interface Otimizada (2x2)**: Novo design de grid dinâmico exibindo os LEDs de sinal e gravação para cada câmera de forma separada.
- **Logs Individuais**: Criação dos arquivos `c1_erros.log` e `c2_erros.log` para isolar eventos de cada câmera.

### [v2.6] - Visualizador Web Lado a Lado
- **Monitor Lado a Lado**: Criação do arquivo `visualizador.html` que junta as duas transmissões WebRTC lado a lado com latência zero.
- **IP Remoto Portátil**: Adicionado painel de configurações na página HTML para permitir assistir de outros PCs na mesma rede. Basta digitar o IP do PC da câmera (`192.168.7.12`).
- **Memória de Configuração**: Uso do `localStorage` do navegador para salvar o IP digitado, evitando reconfigurar.
- **Atalho Direto no Painel**: Botão "Monitor Lado a Lado" incorporado na interface do gerenciador para abrir o monitor no navegador automaticamente.

### [v3.0] - Portabilidade & Contingência Total (Atual)
- **Caminhos 100% Dinâmicos**: O projeto não possui mais caminhos rígidos de pastas (como `C:\Users\Thiesen`). Pode ser colocado em qualquer diretório ou rodado em qualquer computador.
- **Backup de Sincronização Automático**: Em caso de erros com o Google Drive (disco G: offline, falta de espaço ou erro de permissão "Viewer/Leitor"), o script salva automaticamente os arquivos em uma pasta local do PC (`backup_gravacoes`), protegendo contra perda de filmagem.
- **Instalador de Inicialização Dinâmico**: Adicionado botão no painel para gerar o script `.vbs` de inicialização automático ajustado para o caminho atual e usuário local com apenas um clique.
- **Substituição do WMIC por API Nativa**: O gerenciador agora verifica e finaliza processos de gravação usando chamadas de sistema eficientes em memória (ctypes), melhorando drasticamente o desempenho e a estabilidade da interface Tkinter.