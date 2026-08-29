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
Analises fica dentro da mesma janela e possui tres areas: `Operacao`,
`Evidencias` e `Rede e relatorios`. Operacao agrupa Visao geral e Cameras;
Rede e relatorios mantem suas duas visoes em subabas. Evidencias usa uma unica
area com galeria rolavel e subabas de largura total para Atividade e trajetos e
Pessoas observadas. Alternar entre as abas nao reinicia o NVR, o coletor, a
visao ou o banco local.

O historico persistente usa SQLite em `sistema/analytics/`, fora do HD de
gravacoes. A visao reutiliza o preview existente, calibra de forma limitada o
ruido de movimento de cada camera e, a cada cinco segundos, mede contagem de
pessoas, pico, variacao visual e permanencia observada com NanoDet local. Rostos
recorrentes podem formar agrupamentos locais provisórios (`Pessoa 1`, `Pessoa 2`),
mas nome real e funcao continuam manuais e exigem confirmacao explicita.

A area Pessoas observadas mostra agrupamentos provisórios e perfis consentidos,
ordenando os confirmados por visitas e tempo observado estimado. Confirmacoes
simultaneas em cameras diferentes nao
dobram o tempo e eventos repetidos sao idempotentes. O resumo permanece no banco
local ate a exclusao do perfil; ao excluir, o resumo e os eventos associados
tambem sao removidos com `secure_delete`, checkpoint e compactacao. Um hash
irreversivel impede que uma confirmacao atrasada recrie o perfil. Agrupamentos
provisorios ficam cifrados, limitados a 100 e expiram em 10 dias se nao forem
confirmados. Bancos antigos nao recriam ranking a partir de
eventos historicos; eventos antigos de identidade sao descartados na migracao,
evitando restaurar perfis cuja autorizacao possa ter sido revogada. A exclusao e
a compactacao rodam fora do thread da interface; se a compactacao falhar, uma
pendencia persistente faz nova tentativa na proxima abertura.

A area Atividade e trajetos resume fatos observaveis: variacao visual, duracao,
contagem estimada e a sequencia de identificacoes locais por camera. Um intervalo
sem evento e exibido como `sem confirmacao visual`, nunca
como localizacao conhecida, saida da farmacia ou acao da pessoa. O painel nao
classifica emocao, intencao, honestidade ou produtividade.

A aba Rede identifica o tipo de conexao deste PC, velocidade do link, resposta
do gateway, contadores, variacoes entre amostras e picos relativos ao historico
local. Tambem registra sessoes dos dispositivos vistos no cache de vizinhos do
Windows e dos aplicativos que mantem TCP estabelecido neste computador. O MAC
bruto e descartado e vira somente um identificador HMAC local de 16 caracteres;
a chave da instalacao fica cifrada pelo DPAPI do Windows.
Ausencia no cache nao prova que um equipamento esteja offline; permanencia de
aplicativo mede conexao observada, nao tempo de tela ou uso ativo.

O recurso nao identifica destinos, sites ou conteudo acessado. Nao captura
pacotes, mensagens, senhas, navegacao ou conteudo de outros dispositivos.
Cobertura da loja inteira continua dependendo de telemetria autorizada no
gateway. O servidor
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
superiores `Cameras` e `Analises`; a segunda contem `Operacao`, `Evidencias` e
`Rede e relatorios`. Visao geral e Cameras ficam em Operacao, enquanto Rede e
Relatorios compartilham a ultima area. Evidencias concentra galeria, atividade,
trajetos e pessoas, com as analises em subabas de largura total. Trocar de aba
preserva a coleta, os previews e o historico; o fluxo normal nao abre navegador
nem uma segunda janela.

O historico fica em `sistema/analytics/`, fora do HD de gravacao. A visao usa os
quadros ja decodificados pelo preview, sem abrir outra conexao com as cameras e
sem alterar `/api/stream.ts`. Para instalar ou verificar o runtime local fixado
do OpenCV e os modelos com SHA-256 aprovado:

```powershell
python tools\setup_wimi_vision.py
python tools\setup_wimi_vision.py --verify-only
```

O cadastro facial confirmado exige consentimento explicito e um quadro recente
com exatamente um rosto. Nome e vetor ficam juntos em um banco biometrico
separado, protegido pelo DPAPI do Windows. A
funcao da pessoa (`employee`, `manager`, `contractor` ou `authorized`) e
selecionada manualmente no cadastro e permanece dentro do payload protegido.
Antes do cadastro, tres observacoes compatíveis em quadros diferentes podem criar
um agrupamento provisório com nome tecnico `Pessoa N`, vetor e recorte facial
cifrados. Ele nao recebe nome real nem funcao automaticamente, tem limite de 100
itens e expira em 10 dias. Perfis anteriores a essa
classificacao continuam validos como `authorized`. Quando um perfil e
confirmado, a aba `Cameras` mostra nome e funcao cadastrada. Essa aba tambem
lista as 200 confirmacoes mais recentes, com camera, horario e confianca, e
resume quantas vezes cada perfil apareceu. O botao de analise muda sozinho para
`Analise ja ativa` ou `Analise calibrando`, evitando solicitar uma ativacao que
ja aconteceu.

No preview ao vivo e na tela cheia, cada rosto detectado recebe uma caixa
transitoria. Perfis consentidos mostram nome, funcao cadastrada e confianca;
rostos sem correspondencia mostram `Desconhecido`; agrupamentos recorrentes
mostram `Pessoa N | Em analise` em amarelo. A identificacao usa o
mesmo quadro ja entregue ao preview, e atualizada no maximo duas vezes por segundo,
fica somente em memoria por ate 2,5 segundos e nao altera nem grava a camada
visual nos arquivos de video. O bloqueio de hardware continua
podendo pausar a analise sem interromper a gravacao.

O preview e a fila de visao mantem somente o quadro mais recente quando o PC nao
consegue acompanhar a chegada, evitando acumular atraso ao longo das horas. A
tela Cameras mostra separadamente o atraso de fila e o tempo de processamento
da IA. O agrupamento provisório combina vetor facial e continuidade espacial da
mesma camera por uma janela curta; empates só sao reaproveitados quando os
proprios agrupamentos tambem sao compatíveis. Isso reduz fragmentacao, mas nao
torna reconhecimento facial infalivel: nome real e funcao continuam exigindo
confirmacao humana.

O YuNet usa limiar de deteccao `0.80`, calibrado localmente com um quadro real da
camera em 28/08/2026: o limiar anterior `0.90` nao encontrou o rosto distante no
recorte do preview. A entrada permanece limitada a `960x540`, no maximo oito
rostos por quadro, e um agrupamento ainda exige tres observacoes distintas.

Um agrupamento provisório pode ser nomeado pelo cartao da captura ou em Pessoas
observadas. A confirmacao promove o agrupamento para um novo perfil consentido e
move, em uma transacao local, os vinculos de captura e o historico operacional
para o identificador confirmado.
Capturas de atendimento continuam sendo criadas mesmo antes de uma identidade
provisoria existir, desde que todas as caixas faciais estejam completas. Elas
preservam
um quadro de contexto legivel de ate `1280x720` em JPEG 82 e achatam somente as
regioes ampliadas de todos os rostos detectados antes da persistencia. Quando um
rosto e detectado, o sistema tambem guarda uma prancha facial nítida para revisao
exclusivamente local; o vinculo de identidade so e incluido quando ja existe uma
correspondencia segura. Contexto e prancha sao cifrados com DPAPI, contam juntos
no teto operacional de 768 MB e expiram em 10 dias. O teto considera duas
cameras, uma captura a cada 15 minutos e margem sobre os tamanhos medidos; ao
atingi-lo, novas evidencias param sem apagar as existentes. O painel informa a
exclusao
automatica em 10 dias e apresenta as capturas em uma galeria responsiva. Cada
card mostra contexto, recorte de revisao, identificacao provisória ou confirmada,
camera, horario, tamanho e expiracao. Clicar na miniatura abre contexto e rostos
em tamanho adaptado a tela; a roda do mouse funciona sobre imagens, textos e
cartoes. A interface permite selecionar varias capturas, marcar ou desmarcar
todas e confirmar uma exclusao em lote. Sao mantidas no maximo 24 miniaturas
descriptografadas em memoria por pagina e elas so sao abertas quando a aba
Evidencias esta visivel; marcar tudo inclui o historico listado sem carregar
todas as imagens. O vinculo usa apenas `profile_id`; nomes nao sao copiados para
o arquivo. Capturas antigas nao sao reprocessadas e continuam com a protecao da
versao em que foram criadas.

O NanoDet quantizado do OpenCV Zoo usa o mesmo quadro do preview, sem nova
conexao com a camera, e roda no maximo uma vez a cada cinco segundos por camera.
A area `Atividade e trajetos` apresenta a contagem estabilizada, inicio e fim
de presenca, pico, permanencia observada e uma linha do tempo derivada das
confirmacoes de perfis consentidos. Confirmacoes repetidas na mesma camera formam
uma janela observada; uma confirmacao posterior em outra camera forma uma
sequencia temporal, sem afirmar deslocamento. Confirmacoes no mesmo segundo sao
marcadas como simultaneas. Depois de 180 segundos sem nova confirmacao, o
intervalo fica explicitamente inconclusivo. A consulta usa indice dedicado, le
Apenas perfis ainda presentes no cadastro consentido atual podem aparecer. Um
evento atrasado de perfil excluido permanece oculto. Timestamps com fuso sao
normalizados para o horario local antes da persistencia e empates usam
`event_id`, preservando uma ordem deterministica. A consulta le no maximo 500
confirmacoes recentes e nao grava novos eventos, imagens ou trajetorias no HD.
A intensidade de variacao visual aparece apenas no estado atual da camera.
Esses sinais exigem revisao humana e nao inferem emocao, intencao, desonestidade
ou produtividade. A adaptacao automatica aprende somente o ruido visual de fundo
dentro de janela e limites fixos; nao altera gravacao, retencao, camera, rede ou
identidade.

O sistema ainda nao afirma acoes individuais como `pegou o celular`. Um recurso
desse tipo deve primeiro detectar apenas um evento possivel, manter o trecho
para revisao humana, usar zonas calibradas e passar por benchmark prolongado no
hardware real. Nenhum framework temporal pesado foi adicionado ao processo do
NVR nesta entrega.

A aba Rede registra sessoes dos adaptadores deste computador, dispositivos
vistos na LAN e aplicativos com TCP estabelecido neste PC: inicio, ultimo sinal,
duracao observada, contagens e bytes agregados para Cabo ou Wi-Fi. Nao coleta
endereco remoto, pacote, pagina, mensagem, senha ou conteudo. A visao da LAN e
parcial ate existir integracao autorizada com o roteador. O SQLite permanece
local, usa retencao operacional de 90 dias e serializa todas as escritas pelo
mesmo bloqueio do processo.

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
