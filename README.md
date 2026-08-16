# 🎥 NVR Inteligente Câmeras Farmácia — Versão 4.13

Este projeto é uma solução completa de NVR (Network Video Recorder) local e híbrida de baixíssimo consumo de hardware. Ele foi projetado para capturar, gravar, monitorar e gerenciar câmeras inteligentes compatíveis com o ecossistema Tuya, Smart Life e Positivo, com foco em segurança, portabilidade e tolerância a falhas.

---

## 🛠️ Stack Tecnológica & Dependências

- **Linguagem principal:** Python 3.x, executado via `pythonw.exe` para modo silencioso.
- **Interface gráfica:** Tkinter, com GUI customizada em tema escuro premium e responsivo.
- **Processamento de imagem:** Pillow (PIL) para renderização frame a frame.
- **Ponte RTSP:** go2rtc, serviço interno que gerencia a conexão P2P com a nuvem Tuya.
- **Processamento de mídia:** FFmpeg para validação, remuxing bruto e transcodificação sob demanda quando necessário.
- **APIs de integração:** Ctypes do Windows para controle de suspensão de energia e leitura de bateria.
- **TTS (Text-To-Speech):** PowerShell + `SAPI.SpVoice` para feedbacks de voz nativos.

---

## ⚙️ Principais Funcionalidades

### 1. Gravação com Baixo Consumo (0% CPU de Transcodificação)

O gravador consome `/api/stream.ts?src=NOME`, fornecido pelo modulo `mpegts`
do go2rtc, e grava os bytes diretamente em arquivos `.ts`. O inicio da
gravacao e bloqueado se essa rota nao estiver registrada, evitando o falso
estado "gravando" com arquivo vazio.

As gravacoes sao organizadas em pastas diarias (`AAAA-MM-DD`). O temporario
`.recording` fica no mesmo volume do destino validado e e publicado por
renomeacao atomica, sem gravar o bloco inteiro primeiro no SSD do Windows.

### 2. Sincronização e Contingência Offline Inteligente

- **Destino primário:** HD configurado pelo aplicativo e vinculado ao serial do volume. Um disco com o mesmo nome não é aceito automaticamente.
- **Backup local automático:** se o HD estiver offline, sem espaço ou inacessível, as gravações são desviadas para `backup_gravacoes`.
- **Sincronizador em background:** uma thread monitora a disponibilidade do HD. Quando a conexão volta, os arquivos locais são enviados para o destino correto e removidos do backup somente após validação.

### 3. Visualização ao Vivo de Baixíssima Latência (MJPEG Stream)

O painel principal exibe vídeo ao vivo por MJPEG stream persistente com Keep-Alive. O app decodifica frames buscando os marcadores JPEG de início e fim no buffer de bytes, reduzindo a latência para cerca de 50 ms em condições normais de rede.

O grid é responsivo e preserva a proporção original de 16:9:

- Com duas câmeras abertas, o layout reduz os players para caberem lado a lado.
- Com uma câmera aberta, o player expande para o maior tamanho útil disponível.
- O enquadramento é preservado, sem distorção ou corte.

### 4. Gestão de Energia e Quedas de Eletricidade

- **Prevenção de suspensão:** usa `SetThreadExecutionState` para manter o sistema acordado. No modo silencioso, a tela pode apagar normalmente; o comportamento padrão é restaurado ao fechar.
- **Monitor de queda de energia:** usa `GetSystemPowerStatus` para detectar operação em bateria ou nobreak.
- **Desligamento seguro:** se a energia cair e a bateria chegar ao limite crítico (`<= 20%`), o sistema:
  1. Salva e encerra os gravadores de forma limpa para evitar corrupção.
  2. Emite alerta de voz nativo.
  3. Desliga o computador com segurança via comando do Windows.

### 5. Prevenção de Duplicidade de Rede & Limpeza

- **Heartbeat JSON:** a cada 30 segundos, o gravador envia batimentos cardíacos para a pasta de destino. Se outro PC tentar gravar a mesma câmera no mesmo diretório, o conflito é detectado e o segundo processo encerra a gravação automaticamente.
- **Auto-escaneamento:** a cada 3 horas, o sistema verifica apenas arquivos novos ou alterados. A quarentena exige duas falhas e permanece no mesmo disco.
- **Auto-diagnóstico:** a cada 6 horas, gera um relatório de integridade em `diagnostico.txt`.
- **Logs coloridos:** o painel mostra mensagens por tipo: informação, sucesso, aviso e erro, com limpeza automática limitada a 200 linhas.
- **Feedback de voz:** avisos como "Gravando" e "Gravação parada" são disparados em segundo plano.

### 6. Atualização Verificável

O gerenciador compara a versão local com a versão publicada no GitHub, mas só oferece a instalação quando os hashes SHA-256 da versão remota já foram aprovados no arquivo local de configuração. Uma atualização sem hashes aprovados é apenas informada e não interrompe a gravação.

### 7. Retenção e Espaço em Disco

- A rotacao automatica preserva os ultimos 90 dias por padrao.
- O HD principal exige ao menos 15 GB livres. O fallback local preserva por
  padrao a maior reserva entre 20 GB e 10% do volume do Windows.
- Pouco espaco no `C:` nao bloqueia um HD principal valido; o `C:` so e usado
  quando o fallback local for realmente necessario.
- A exclusao emergencial e desativada por padrao e nunca remove gravacoes mais
  novas que a retencao configurada.

### 8. Painel Unificado WIMI Analytics

O painel principal possui duas abas superiores: **Cameras** e **Analises**.
Analises fica dentro da mesma janela e contem Visao geral, Cameras,
Comportamento, Rede, Relatorios e Pessoas. Alternar entre elas nao reinicia o
NVR, o coletor, a visao ou o banco local.

O historico persistente usa SQLite em `sistema/analytics/`, fora do HD de
gravacoes. A visao reutiliza o preview existente, calibra de forma limitada o
ruido de movimento de cada camera e nunca cria perfis faciais sozinha. Cadastro
de identidade continua manual e consentido.

A aba Pessoas ordena somente os perfis consentidos por visitas e tempo observado
estimado. Confirmacoes simultaneas em cameras diferentes nao dobram o tempo e
eventos repetidos sao idempotentes. O resumo permanece no banco local ate a
exclusao do perfil; ao excluir, o resumo e os eventos associados tambem sao
removidos com `secure_delete`, checkpoint e compactacao. Um hash irreversivel
impede que uma confirmacao atrasada recrie o perfil; rostos desconhecidos
continuam anonimos. Bancos antigos nao recriam ranking a partir de eventos
historicos; eventos antigos de identidade sao descartados na migracao, evitando
restaurar perfis cuja autorizacao possa ter sido revogada. A exclusao e a
compactacao rodam fora do thread da interface; se a compactacao falhar, uma
pendencia persistente faz nova tentativa na proxima abertura.

A aba Comportamento resume apenas fatos observaveis, como movimento, duracao e
contagem de rostos. Nao classifica emocao, intencao, honestidade ou produtividade.

A aba Rede identifica o tipo de conexao deste PC, velocidade do link,
contadores, variacoes entre amostras e picos relativos ao historico local. Ela
pode afirmar que houve trafego agregado neste computador, mas nao identifica
destinos, sites ou conteudo acessado. Nao captura pacotes, mensagens, senhas,
navegacao ou conteudo de outros dispositivos. Cobertura da loja inteira continua
dependendo de fonte autorizada no gateway. O servidor
`http://127.0.0.1:8765/` permanece apenas para compatibilidade e diagnostico.
O coletor Windows permanece limitado a 12 segundos e usa cache, evitando criar
PowerShell continuamente quando o CIM estiver lento.

---

## 📂 Estrutura do Projeto

```text
camera farmacia/
├── sistema/
│   ├── go2rtc/
│   │   ├── go2rtc.exe
│   │   ├── go2rtc.yaml         # local, gerado e ignorado pelo Git
│   │   └── ffmpeg.exe
│   ├── web/
│   │   └── visualizador.html   # publicação segura do visualizador
│   ├── backup_gravacoes/
│   ├── gravando_temp/
│   ├── logs/
│   └── config.json             # local, contém segredos e identidade do HD
├── wimi_analytics/             # API local, supervisor, bridge e painel unificado
├── gerenciador.pyw
├── README.md
└── .gitignore
```

---

## 💻 Configuração e Execução

### 1. Instalar Python e Pillow

Instale o Python 3.10 ou superior e marque a opção **Add Python to PATH** durante a instalação.

Depois instale a dependência de imagem:

```bash
pip install Pillow
```

### 2. Configurar o go2rtc

Não edite `sistema/go2rtc/go2rtc.yaml`: ele é gerado a partir da configuração local e é substituído ao iniciar. As credenciais e os identificadores das câmeras ficam somente em `sistema/config.json`, que é ignorado pelo Git.

O gerenciador valida os nomes e as URLs dos streams, gera uma senha local forte para a interface web e publica somente `visualizador.html` na porta do go2rtc. A API, RTSP e o visualizador na rede local exigem autenticação; o navegador solicitará as credenciais ao abrir o painel remoto.

### 3. Configurar o destino das gravações

Na instalação atual, o destino configurado é:

```text
D:\farmacia camera
```

Esse caminho também pode ser ajustado pela interface gráfica ou pelo arquivo local `config.json`.

Ao confirmar o destino, o NVR guarda a identidade do volume. Para trocar o HD intencionalmente, selecione o novo caminho no aplicativo enquanto o volume estiver conectado.

### 4. Atualizações e dependências

Instale o Pillow manualmente antes do primeiro uso. O NVR não executa `pip install` sozinho durante a gravação.

Os binários fixados de go2rtc e FFmpeg são aceitos somente quando o SHA-256 confere. A atualização automática do código exige, em `config.json`, os hashes aprovados para a versão desejada em `trusted_update_hashes`; sem essa aprovação, faça a atualização de forma assistida e validada.

### 5. Executar o painel

Abra:

```text
gerenciador.pyw
```

Para rodar silenciosamente, use:

```bash
pythonw.exe gerenciador.pyw --silent
```

O WIMI Analytics roda integrado ao processo do NVR. No modo visual, use as abas
superiores `Cameras` e `Analises`; a segunda contem seis subabas: Visao geral,
Cameras, Comportamento, Rede, Relatorios e Pessoas. Trocar de aba preserva a
coleta, os previews e o historico; o fluxo normal nao abre navegador nem uma
segunda janela.

O historico fica em `sistema/analytics/`, fora do HD de gravacao. A visao usa os
quadros ja decodificados pelo preview, sem abrir outra conexao com as cameras e
sem alterar `/api/stream.ts`. Para instalar ou verificar o runtime local fixado
do OpenCV e os modelos com SHA-256 aprovado:

```powershell
python tools\setup_wimi_vision.py
python tools\setup_wimi_vision.py --verify-only
```

O cadastro facial exige consentimento explicito e um quadro recente com
exatamente um rosto. Nenhuma imagem e salva; nome e vetor ficam juntos em um
banco biometrico separado, protegido pelo DPAPI do Windows. Movimento, contagem
de rostos e reconhecimento sao evidencias para revisao humana, nao inferencias
de emocao, intencao ou produtividade. A adaptacao automatica aprende somente o
ruido visual de fundo dentro de janela e limites fixos; nao altera gravacao,
retencao, camera, rede ou identidade.

O servidor HTTP antigo continua disponivel somente para compatibilidade e
diagnostico. Para executa-lo sem iniciar o NVR ou as cameras, use:

```powershell
python -m wimi_analytics.server
```

O endereco aceita somente a interface local. `/healthz` expoe apenas readiness;
as APIs `/api/v1/*` exigem a sessao criada pela pagina, validam `Host` e
`Origin` e nao habilitam CORS externo.

---

## 🤖 Diretrizes para Manutenção por IA ou Desenvolvedores

1. **Preserve a gravação direta.** A gravação deve consumir `/api/stream.ts?src=NOME` via `urllib.request`, sem OpenCV, decode local ou re-encode contínuo.
2. **Use `127.0.0.1` para a API local.** Evite `localhost` para não depender de resolução IPv6 do Windows.
3. **Mantenha o MJPEG em thread separada.** A GUI Tkinter não deve bloquear enquanto lê frames.
4. **Feche streams recolhidos.** Ao recolher uma câmera ou fechar a janela, encerre conexões HTTP e loops de leitura.
5. **Não quebre as pastas diárias.** Gravações e sincronização devem respeitar subpastas `AAAA-MM-DD`.
6. **Proteja contra duplicidade.** O heartbeat JSON é parte crítica da segurança de gravação em rede.
7. **Evite dependências pesadas.** O projeto foi desenhado para rodar com baixo consumo em máquinas simples.
8. **Não publique configurações.** `go2rtc.yaml`, `config.json`, backups de configuração e a pasta `sistema/web` não pertencem ao Git nem a compartilhamentos externos.
9. **Não amplie a limpeza automática.** A retenção e qualquer exclusão emergencial exigem política explícita; nunca apague gravações pendentes ou recentes para recuperar espaço.

---

## Diagnostico de Saude

O NVR v4.13 possui um avaliador automatico executado em segundo plano. Ele
correlaciona espaco local, disponibilidade do HD, backups pendentes, threads de
gravacao, ultimo byte recebido por camera, reconexoes, memoria, energia, status
basico informado pelo Windows e relatorios `Kernel_144`.

O estado `ONLINE` de cada camera exige evidencia de midia: produtor ativo no
go2rtc, bytes recentes na gravacao ou frame recente no painel. Durante uma
oscilacao, a interface passa por `RECONECTANDO` e somente conclui `OFFLINE`
apos 90 segundos sem dados ou dez amostras sem midia em uma visualizacao ativa.
A recuperacao exige duas amostras positivas. Sem gravacao ou visualizacao ativa,
o estado e `EM ESPERA`, evitando declarar uma camera offline sem ter sido medida.
As mesmas evidencias ficam em `metrics.camera_connectivity` no snapshot JSON.
Uma conexao encerrada sem entregar midia aguarda antes de tentar novamente, e o
heartbeat do gravador continua limitado a uma renovacao a cada 30 segundos.

O cabecalho e os cards distinguem uma thread de gravacao ativa de uma camera que
realmente entrega midia. `GRAVANDO` verde exige dados recentes; uma thread sem
bytes aparece como `CONECTANDO`, `RECONECTANDO` ou `SEM DADOS`, e a transmissao
nao permanece verde quando a camera esta offline. A faixa lateral do card e o
contorno da analise seguem a mesma gravidade para facilitar a leitura rapida.
Uma transicao para `OFFLINE` invalida a coleta de saude anterior, registra
`CAMERA_OFFLINE` no snapshot e atualiza a analise sem aguardar o ciclo normal.
O estado tambem preserva `status_since` e `last_recovered_at`: os cards mostram
ha quanto tempo a camera esta sem midia e deixam explicito que a reconexao
automatica continua ativa. Quando a midia retorna, duas amostras positivas
confirmam a recuperacao antes de voltar ao verde.

Tentativas vazias de gravacao usam espera progressiva de 2, 4, 8 e no maximo 15
segundos. Qualquer byte recebido restaura imediatamente a espera base de 2
segundos. Isso reduz conexoes repetidas durante uma indisponibilidade longa sem
desistir da camera. Os contadores de amostras ficam limitados a 120.

Na interface, cameras recolhidas nao reservam mais grandes areas vazias. O painel
mostra a atividade recente em cada card e inclui o horario da ultima coleta no
`Diagnostico Operacional`, facilitando distinguir informacao atual de estado
antigo.

Para operacao continua, a busca da ultima gravacao e armazenada em cache por 30
segundos para cada camera e caminho. O verificador percorre os arquivos sem criar
uma lista completa em memoria. A deteccao de erro duplicado le somente os 16 KiB
finais do log, e o estado interno de deduplicacao fica limitado a 500 mensagens.

Sobre esses sinais existe uma inteligencia operacional local. Ela nao usa API
externa e nao envia imagens, credenciais ou logs para a internet. A cada coleta,
ela correlaciona sintomas e informa:

- causa provavel e nivel de confianca heuristico, baseado na regra acionada;
- explicacao da correlacao encontrada;
- ate tres acoes em ordem de prioridade;
- recomendacao de continuar monitorando ou encerrar com seguranca;
- permissao para o scanner automatico executar manutencao pesada.

O painel mostra a conclusao em `Analise Inteligente`. O JSON inclui a secao
`intelligence`. Logs `[INTELLIGENCE]` sao gravados somente quando a conclusao
muda, evitando escrita repetitiva no disco.

O ultimo estado e publicado atomicamente em:

```text
sistema\logs\health_status.json
```

Tambem e possivel executar uma coleta sem abrir a interface, iniciar cameras ou
alterar gravacoes:

```powershell
python gerenciador.pyw --health-check
```

Codigos de saida: `0` saudavel, `1` aviso, `2` critico e `3` falha do proprio
diagnostico. O status SMART generico do Windows nao substitui a ferramenta do
fabricante do disco.

## Ensaio Real Controlado

Mudancas que afetam gravacao, go2rtc, encerramento ou armazenamento podem ser
validadas por um ensaio real com limite de 30 a 1800 segundos. O modo e
silencioso, inicia as cameras configuradas e usa o mesmo encerramento seguro do
uso normal ao atingir o limite:

```powershell
python gerenciador.pyw --smoke-test-seconds 180
```

Para interromper antecipadamente sem usar `taskkill`:

```powershell
python gerenciador.pyw --safe-stop
```

O comando de parada aceita conexao somente na interface local `127.0.0.1`. Um
ensaio deve ser precedido por `--health-check` e seguido pela verificacao de
processos, temporarios, arquivos publicados, espaco livre e novos relatorios
`Kernel_144`.

O monitor operacional automatiza essa linha de base, mede CPU, memoria e
threads, valida somente os videos criados pelo ensaio e confirma que nao
sobraram processos ou artefatos:

```powershell
.\tools\smoke_test_monitor.ps1 -Seconds 30
```
