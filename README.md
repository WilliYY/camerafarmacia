# 🎥 Controle e Gravação da Câmera - Farmácia (v2.5 Dupla Premium)

Este projeto foi desenvolvido para capturar o sinal de vídeo de uma **Câmera Inteligente Positivo (ecossistema Tuya)** na rede local, redimensionar a imagem para salvar espaço e gravar continuamente em blocos exatos de 30 minutos sincronizados com o relógio diretamente em uma pasta do Google Drive (`G:\Meu Drive\CAMERAS\CAMERA 1 FARMACIA`).

---

## 🛠️ Como Funciona o Sistema

O sistema é composto por três componentes principais trabalhando juntos:

1. **Ponte RTSP (`go2rtc.exe`)**:
   As câmeras Positivo/Tuya possuem portas locais de transmissão bloqueadas de fábrica. O `go2rtc` estabelece uma conexão autenticada com os servidores em nuvem da Tuya e cria uma transmissão RTSP local em `rtsp://localhost:8554/farmacia`.
   
2. **Script de Gravação (`gravador_camera.py`)**:
   Um script em Python que lê a transmissão local do `go2rtc`, redimensiona cada quadro para **HD (1280x720)** para otimizar espaço de armazenamento e salva os arquivos com nomes no formato `camera_AAAA-MM-DD_HH-MM_ate_HH-MM.mp4`. Os arquivos são finalizados e salvos nos minutos `:00` e `:30` do relógio.

3. **Interface Gráfica (`gerenciador.pyw`)**:
   Uma interface amigável escrita em Python (Tkinter) com tema escuro que permite ao usuário iniciar, parar, monitorar o status do sistema (com LEDs de status virtuais), consultar o IP local e gerar relatórios de diagnóstico.

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

### Passo 5: Inicialização Automática com o Windows (Opcional)
Para que a gravação inicie de forma invisível em segundo plano toda vez que o Windows ligar:
1. Pressione `Win + R`, digite `shell:startup` e dê Enter. Isso abrirá a pasta de Inicialização do Windows.
2. Copie o arquivo `iniciar_gravacao_farmacia.vbs` para dentro desta pasta.
3. Abra o arquivo `.vbs` com o Bloco de Notas e certifique-se de que os caminhos contidos nele batem com o local onde você salvou a pasta no novo PC.

### Passo 6: Acessar a Transmissão de Outros Dispositivos (Opcional)
Se você deseja assistir à câmera a partir de outros PCs na mesma rede:
1. No PC que está gravando, abra a pasta do projeto.
2. Clique com o botão direito sobre o arquivo `Liberar Rede Local (Executar como Admin).bat` e selecione **"Executar como Administrador"**.
3. O script irá abrir as portas `1984` e `8554` no Firewall do Windows.
4. Agora, em qualquer outro aparelho (PC, celular, tablet) conectado no mesmo Wi-Fi, acesse o link do navegador indicado na interface (Ex: `http://192.168.7.12:1984`).

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

### [v2.5] - Suporte Duplo de Câmeras (Atual)
- **Sistema Duplo Unificado**: Expansão da interface para gerenciar a **Câmera 1** e a **Câmera 2** simultaneamente em um painel unificado.
- **Parametrização por CLI**: O script `gravador_camera.py` agora aceita argumentos (`--stream`, `--dir`, `--lock`, `--log`), permitindo rodar duas instâncias independentes a partir do mesmo arquivo.
- **Interface Otimizada (2x2)**: Novo design de grid dinâmico exibindo os LEDs de sinal e gravação para cada câmera de forma separada.
- **Logs Individuais**: Criação dos arquivos `c1_erros.log` e `c2_erros.log` para isolar eventos de cada câmera.