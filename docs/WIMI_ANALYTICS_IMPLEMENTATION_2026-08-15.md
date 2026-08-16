# WIMI Analytics - integracao nativa e visao local

Atualizado em: 16/08/2026

## Resultado

O WIMI Analytics esta integrado ao painel Tkinter do NVR. O fluxo normal nao
inicia nem abre um site local ou uma segunda janela. O topo possui as abas
`Cameras` e `Analises`; a segunda preserva coleta, selecao e historico ao
alternar entre seis subabas:

- Visao geral;
- Cameras;
- Comportamento;
- Rede;
- Relatorios;
- Pessoas.

O servidor HTTP em `127.0.0.1:8765` permanece apenas como compatibilidade de
diagnostico. Ele nao faz parte do fluxo normal do operador.

## Arquitetura entregue

```text
gerenciador.pyw
  |-- gravacao direta: /api/stream.ts?src=NOME
  |-- preview MJPEG existente
  |     `-- amostra limitada para VisionCoordinator
  |-- health_status.json sanitizado
  `-- WIMI nativo
        |-- AnalyticsCollector
        |-- AnalyticsStore (relatorios, rede e eventos)
        |-- VisionCoordinator (movimento, rosto e identidade)
        |-- BiometricStore + DPAPI (perfis consentidos)
        `-- AnalyticsDesktopWindow (frame embutido com seis subabas Tkinter)
```

O coletor e a visao nao controlam gravacao, retencao, go2rtc, energia ou HD. O
`gerenciador.pyw` continua sendo o unico dono desses fluxos.

## Persistencia e privacidade

- `sistema/analytics/wimi_analytics.sqlite3`: relatorios sanitizados, contadores
  agregados de rede deste PC e transicoes de visao;
- `sistema/analytics/wimi_biometrics.sqlite3`: perfil biometrico separado;
- bancos com limite de tamanho, WAL limitado, `busy_timeout` e conexoes curtas;
- retencao operacional de 90 dias, sem tocar em diretorios de video;
- relatorio persistido por mudanca ou intervalo de seguranca, reduzindo escrita;
- rede sem captura de pacote, DNS consultado, pagina, mensagem, senha, IP ou MAC
  persistido;
- perfis protegidos pelo DPAPI do usuario Windows;
- exclusao biometrica usa `secure_delete`, `VACUUM` e truncamento do WAL;
- nenhuma imagem de camera e gravada pelo Analytics.

## Visao computacional

- movimento deterministico por diferenca de quadros, histerese e calibracao
  adaptativa limitada por camera;
- no maximo uma amostra por segundo por camera e dois quadros no total na fila;
- amostra reduzida antes de entrar na fila, com limite de `1280x720`;
- YuNet/SFace executados no maximo a cada tres segundos por camera;
- entrada facial limitada a `960x540` e oito rostos por quadro;
- fila recupera de erro transitorio sem perder permanentemente o worker;
- quadro de cadastro expira apos cinco segundos;
- cadastro roda fora da thread Tk e exige consentimento e exatamente um rosto;
- reconhecimento exige limiar, margem contra o segundo perfil e duas
  confirmacoes consecutivas;
- identidade nao cadastrada continua anonima e nenhum perfil e criado sozinho;
- a calibracao aprende somente variacao visual pequena, possui janela finita,
  piso/teto de limiar e timeout; nunca controla gravacao ou retencao.

Os modelos locais e o runtime isolado sao instalados por
`tools/setup_wimi_vision.py`. O manifesto fixa versoes, origem, tamanho e
SHA-256. Esses artefatos gerados ficam fora do Git.

## Protecao de operacao 24h

- a visao reutiliza o preview e nao cria uma segunda conexao RTSP/MJPEG;
- nenhum decode ou re-encode foi adicionado ao caminho de gravacao;
- a analise pausa durante encerramento, indisponibilidade do HD, novo
  `Kernel_144` da sessao, memoria excessiva ou deterioracao de memoria;
- timeout de encerramento preserva a referencia da thread ainda ativa;
- Tkinter e destruido somente pela thread principal;
- cadastro facial lento nao bloqueia a interface;
- falhas de OpenCV ou SQLite por quadro recebem backoff e log limitado.

## Rede e computadores

O diagnostico consulta configuracao e contadores agregados dos adaptadores deste
PC. Exibe cabo/Wi-Fi/virtual, velocidade, bytes, pacotes, erros e descartes,
alem de taxas e variacoes calculadas entre amostras. Nao observa o trafego
completo da loja e nao captura conteudo.

Na validacao local de 16/08/2026, o Windows informou uma interface `Ethernet`
por cabo a `1 Gbps`, com gateway e DNS configurados. Em uma amostra de dez
segundos nao houve aumento de erros ou descartes; os contadores acumulados
anteriores continuam visiveis como historico, sem serem tratados como falha
nova.

Agente remoto nao e necessario para este computador. Outros computadores so
devem ser incluidos em uma etapa autorizada, com escopo, autenticacao e politica
de privacidade proprios.

## Evolucao de perfis consentidos e rede

O banco operacional ganhou `profile_presence_stats`,
`profile_presence_streams` e `profile_presence_sessions`. As tabelas guardam
somente o identificador local, datas, contagens, tempo estimado e cameras. Uma
sessao compacta representa uma visita e permite mesclar evento atrasado sem
depender do historico sujeito a retencao. Nomes e vetores biometricos continuam
separados e protegidos por DPAPI. A migracao remove eventos antigos de identidade
e nao cria resumos a partir deles, pois a autorizacao correspondente pode ter
sido revogada. A atualizacao normal e transacional e idempotente.

O painel Pessoas mostra os perfis consentidos mais observados. O painel
Comportamento permanece limitado a eventos observaveis e nao tenta inferir
emocao, intencao ou produtividade. Excluir um perfil elimina tambem seu resumo e
os eventos de presenca associados. `secure_delete`, truncamento do WAL,
compactacao e um hash irreversivel de exclusao evitam residuos e recriacao por
confirmacao atrasada. A interface executa esse fluxo em worker unico. Se a
compactacao falhar, `maintenance_state` preserva a pendencia e a inicializacao
seguinte repete a operacao antes de liberar o marcador.

O historico de rede passou a classificar atividade agregada, reset de contadores,
novos erros/descartes e picos relevantes contra a mediana recente. Coleta
indisponivel ou troca de conexao invalida o delta seguinte, evitando pico falso.
O escopo e somente este computador: nenhum destino, site, pacote ou conteudo e
capturado.

## Validacao executavel

```powershell
python -m py_compile gerenciador.pyw
python -m compileall -q wimi_analytics tools tests
python -m unittest discover -s tests -v
python tools\setup_wimi_vision.py --verify-only
python gerenciador.pyw --health-check
python gerenciador.pyw --smoke-test-seconds 180
```

No ensaio real de 180 segundos de 16/08/2026, as duas cameras entregaram dados,
os dois arquivos finais foram validados por remux do FFmpeg, nao houve novo
`Kernel_144`, o HD permaneceu disponivel e nao restaram processos nem artefatos
temporarios. Essa evidencia nao substitui um ensaio supervisionado de 24 horas,
queda de energia ou desconexao USB.

Apos as correcoes finais de concorrencia, o ensaio final de 30 segundos
confirmou duas gravacoes validas, pico total de 252 MB, CPU maxima de 7,6%,
824,36 GB livres, zero novo `Kernel_144`, zero artefato e zero processo residual.
A suite desta evolucao executou 144 testes com sucesso, incluindo migracao
aditiva do banco existente, painel embutido, calibracao adaptativa e deteccao do
tipo de conexao sem persistir IP ou MAC.

O ensaio controlado desta evolucao durou 42 segundos, gerou e validou dois
videos, atingiu pico de 243,3 MB, 66 threads e 5,7% de CPU, manteve 824,35 GB
livres e terminou sem novo `Kernel_144`, processo ou artefato residual. Em um
ensaio visual separado, as duas cameras ficaram Online, passaram de Calibrando
para Ativo 2/2 e mantiveram o processo em 250,5 MB sem iniciar gravacao.

## Limites

- reconhecimento facial pode errar e nunca decide acusacao, seguranca ou medida
  trabalhista sozinho;
- nao ha inferencia de emocao, intencao, desonestidade ou produtividade;
- cobertura de rede permanece limitada a este PC;
- SMART generico do Windows nao substitui diagnostico do fabricante;
- o historico de `LiveKernelEvent 144`/`USBXHCI` continua sendo um risco fisico
  que software apenas detecta e correlaciona;
- estabilidade continua de 24 horas ainda requer janela supervisionada.

## Atualizacao

O atualizador legado baixa apenas `gerenciador.pyw` e `visualizador.html`. Esta
entrega multi-arquivo deve ser instalada pelo checkout completo e validado do
repositorio. Nao elevar a versao anunciada ate existir pacote assinado que
inclua `wimi_analytics/`, modelos e runtime de forma atomica.
