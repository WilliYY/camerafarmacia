# WIMI Desktop, historico e visao local

Data: 16/08/2026

## Objetivo

Entregar as analises dentro do aplicativo Tkinter usado na farmacia, sem abrir
um navegador. O operador deve alternar entre Visao geral, Cameras,
Comportamento, Rede, Evidencias, Relatorios e Pessoas sem perder a coleta em
memoria ou o historico persistido.

O sistema observa somente evidencias tecnicas e operacionais. Movimento,
presenca e identidade cadastrada sao sinais para revisao humana; nao inferem
emocao, intencao, desonestidade, produtividade individual ou qualquer atributo
sensivel.

## Decisoes

1. O painel principal possui abas superiores Cameras e Analises. A interface
   WIMI e embutida na segunda aba e nao cria outra janela.
2. O servidor HTTP local continua disponivel apenas para compatibilidade e
   diagnostico, mas nao e iniciado nem aberto pelo fluxo normal da interface.
3. O historico usa SQLite em `sistema/analytics/wimi_analytics.sqlite3`, sempre
   no disco local do Windows e nunca no HD de gravacoes.
4. Relatorios persistem somente DTOs sanitizados. URLs, credenciais, caminhos,
   hostname, modelo/serial de disco e quadros de camera nao entram no banco.
5. A rede e coletada a cada 60 segundos como configuracao agregada deste PC,
   incluindo tipo de conexao, velocidade, contadores e variacao entre amostras.
   Nao ha captura de pacote, payload, pagina, senha, mensagem ou varredura da
   rede da loja.
6. A visao reutiliza quadros que o preview Tkinter ja decodificou. Nao abre uma
   segunda conexao, nao liga transcodificacao escondida e nao toca no fluxo
   `/api/stream.ts` usado pela gravacao.
7. A fila de visao possui no maximo dois quadros no total e aceita no maximo uma
   amostra por segundo por camera. Quadros antigos sao descartados.
8. Movimento funciona com Pillow e nao depende de modelo. Cada camera passa por
   calibracao local limitada antes dos alertas; o limiar adaptativo tem piso,
   teto, janela finita e nao aprende mudancas grandes como ruido. Pessoa usa o
   NanoDet quantizado do OpenCV Zoo no maximo a cada cinco segundos; rosto e
   identidade usam YuNet/SFace. Todos ficam opcionais e so carregam quando o
   runtime e os modelos com SHA-256 aprovado estiverem presentes.
9. Cadastro facial e manual, consentido e exige exatamente um rosto. Nenhuma
   imagem identificavel e salva; somente o vetor biometrico protegido pelo
   DPAPI do Windows. Capturas operacionais preservam ate `1280x720` em JPEG 82,
   pixelizam o contexto em blocos de 12 pixels e achatam todos os rostos
   detectados antes de qualquer persistencia; nao recebem identidade.
10. Uma identidade so e exibida apos correspondencia acima do limiar, margem
    contra o segundo candidato e confirmacao em amostras consecutivas.
11. A visao pausa se a protecao de hardware bloquear manutencao pesada, se a
    memoria do processo ultrapassar 750 MB ou se o encerramento iniciar.
12. O detector recebe no maximo `960x540` e processa no maximo oito rostos por
    quadro. O quadro usado no cadastro expira em cinco segundos.
13. Cadastro e encerramento nao executam chamadas Tkinter em thread auxiliar.
    Erros transitorios por quadro nao encerram permanentemente a visao.

## Persistencia

Tabelas versionadas:

- `report_snapshots`: relatorio e prontidao sanitizados, por mudanca ou amostra
  de seguranca a cada 15 minutos;
- `network_samples`: estado agregado da rede deste PC, no maximo uma linha por
  minuto e somente quando houver mudanca ou amostra horaria;
- `network_connection_sessions`: sessoes Cabo/Wi-Fi deste host com inicio,
  ultimo sinal, duracao medida e bytes agregados;
- `network_device_sessions`: equipamentos vistos no cache de vizinhos, com IP
  privado, identificador pseudonimo, primeiro/ultimo sinal e duracao observada;
- `local_application_sessions`: aplicativos deste PC com TCP estabelecido,
  inicio/ultimo sinal, duracao observada e pico de conexoes;
- `vision_events`: inicio/fim de movimento, contagem de rostos e pessoas,
  inicio/fim de presenca observada, falha limitada e presenca consentida
  confirmada, sem imagem;
- `evidence_snapshots`: indice sem nome ou perfil para capturas de atendimento
  descaracterizadas e cifradas em diretorio separado.

Os perfis nao ficam no banco operacional. O banco separado
`sistema/analytics/wimi_biometrics.sqlite3` contem `biometric_profiles` e
`biometric_audit`. Nome e vetor estao no mesmo payload DPAPI; a exclusao usa
`secure_delete`, compactacao e truncamento do WAL.

Retencao padrao: 90 dias para amostras e eventos. A limpeza e limitada, roda no
maximo uma vez por dia e nunca acessa diretorios de video. O WAL e limitado e
recebe checkpoint depois da manutencao.

As evidencias visuais usam retencao fixa de 10 dias, uma captura no maximo a
cada 15 minutos por camera e teto de 256 MB. A falta de caixas completas para
todos os rostos impede a persistencia. Ao atingir o teto, a coleta visual para
sem remover itens ainda dentro do prazo. O painel mostra a expiracao e oferece
exclusao manual; nenhum item guarda `profile_id`, nome ou face identificavel.

A analise de comportamento e agregada por camera. Duas amostras consecutivas
sem pessoa encerram uma presenca observada; falta de amostra ou modelo
indisponivel e estado inconclusivo e nao encerra sessao. Nao existe nesta fase
tracking individual, inferencia de emocao, intencao ou produtividade.

## Interface nativa

- **Visao geral:** estado do NVR, riscos, pontos fortes e atualidade da fonte.
- **Cameras:** conectividade, gravacao e estado da analise por camera.
- **Comportamento:** movimento e presenca agregados, com origem e horario.
- **Rede:** conexao/gateway, dispositivos vistos e aplicativos TCP deste PC em
  subabas persistentes.
- **Evidencias:** miniaturas descaracterizadas, expiracao e exclusao manual.
- **Relatorios:** coletas persistidas, filtros de periodo e detalhes.
- **Pessoas:** cadastro, ativacao e exclusao de perfis consentidos.

Trocar de aba apenas muda a visualizacao. O coletor, a fila limitada, os
previews e o banco mantem o estado. A navegacao superior tambem pode ser
percorrida por teclado pelo `ttk.Notebook`.

## Ranking consentido e trafego agregado

- `profile_presence_stats` mantem por perfil consentido primeira e ultima
  observacao, visitas, amostras e tempo observado estimado;
- `profile_presence_streams` registra apenas em quais cameras o perfil foi
  confirmado; nome e vetor continuam exclusivos do banco biometrico protegido;
- `profile_presence_sessions` mantem uma linha compacta por visita e permite
  mesclar amostras atrasadas sem depender dos eventos sujeitos a retencao;
- o resumo e atualizado na mesma transacao do evento e ignora `event_id`
  repetido; confirmacoes simultaneas em cameras diferentes nao dobram o tempo;
- uma lacuna superior a 90 segundos abre uma nova visita e cada primeira amostra
  representa 3 segundos, por isso a interface usa o termo estimado;
- eventos atrasados mesclam somente as sessoes vizinhas do perfil afetado;
- excluir o perfil remove o resumo e os eventos operacionais vinculados, ativa
  `secure_delete`, trunca o WAL e compacta o banco;
- exclusao e compactacao executam em worker unico, sem bloquear o Tkinter;
- `maintenance_state` conserva a pendencia ate a compactacao concluir e repete
  a tentativa na proxima inicializacao;
- um hash irreversivel de exclusao bloqueia confirmacoes atrasadas sem reter o
  identificador original;
- a migracao remove eventos historicos de identidade e nao cria resumos a partir
  deles, pois podem pertencer a perfis cuja autorizacao ja foi revogada;
- rostos sem cadastro consentido permanecem anonimos e nao criam perfil;
- o resumo de rede usa somente bytes por segundo, erros, descartes e reset de
  contadores deste PC. Uma coleta indisponivel ou troca de continuidade invalida
  o delta seguinte. Detecta atividade e picos, mas nao coleta pacote, destino,
  DNS consultado, pagina, mensagem, senha ou conteudo.
- `network_connection_sessions` separa Cabo e Wi-Fi, encerra lacunas maiores que
  cinco minutos e nunca inventa trafego negativo apos reset de contador;
- `network_device_sessions` recebe somente IP privado e identificador derivado;
  o MAC bruto nao e persistido, o identificador usa HMAC com chave DPAPI local e
  ausencia no cache nao prova dispositivo offline;
- `local_application_sessions` mede conexao TCP estabelecida deste PC, nao tempo
  em primeiro plano, destino, site, pagina ou conteudo;
- falha nas fontes de vizinhos ou aplicativos nao encerra sessoes existentes;
- entradas `Stale` nao renovam presenca e os historicos sao limitados a 5.000
  sessoes de dispositivos e 10.000 sessoes de aplicativos;
- a imagem de evidencia e descaracterizada em memoria, codificada, cifrada por
  DPAPI e publicada por temporario, `fsync` e troca atomica;
- a miniatura e descriptografada apenas em memoria e a manutencao nunca percorre
  o acervo de gravacoes.

## Validacao

```powershell
python -m py_compile gerenciador.pyw wimi_analytics\*.py
python -m unittest discover -s tests -v
python gerenciador.pyw --health-check
python gerenciador.pyw --smoke-test-seconds 180
```

O ensaio real de 180 segundos de 16/08/2026 gravou as duas cameras, validou os
dois arquivos finais por remux, manteve o HD disponivel, nao criou novo
`Kernel_144` e terminou sem processos ou temporarios residuais.

Nesta evolucao, um ensaio adicional de 42 segundos validou dois videos sem novo
`Kernel_144` e sem residuos. A visao real tambem colocou as duas cameras Online,
concluiu a calibracao adaptativa em cerca de 30 segundos e chegou a Ativo 2/2,
com memoria abaixo de 251 MB.

Em 23/08/2026, a evolucao de evidencias e comportamento passou por novo ensaio
real controlado de 60 segundos. As duas cameras ficaram Online, dois arquivos TS
finais foram decodificados pelo FFmpeg com retorno zero e sem erro de
decodificacao, o HD permaneceu disponivel, nao houve novo `Kernel_144` e nao
restaram processos, travas ou temporarios de publicacao. O detector de pessoas
tambem processou um quadro real `2304x1296` em 177,7 ms na CPU. O FFmpeg emitiu
avisos de DTS nao monotono ao encaminhar os quadros ao muxer nulo; a observacao
foi preservada para uma auditoria futura de timestamps e nao foi tratada como
prova de corrupcao.

Antes do teste real: confirmar uma unica instancia, baseline de discos,
processos e Kernel_144. Interromper se surgir novo Kernel_144, se o HD sair, se
a memoria superar 750 MB ou se uma gravacao parar de crescer.

## Limites desta entrega

- O agente dos computadores continua opcional e nao sera instalado sem uma
  etapa propria.
- A cobertura de rede continua restrita a este PC ate existir integracao
  autorizada com o gateway.
- Reconhecimento facial pode errar. Nunca deve ser usado sozinho para decisao
  trabalhista, seguranca ou acusacao.
- Estabilidade de 24 horas exige ensaio supervisionado posterior.
