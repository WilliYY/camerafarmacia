# Regras do Projeto NVR Camera Farmacia

## Missao

Este projeto e um NVR Windows destinado a operacao continua. A prioridade e
preservar gravacoes, evitar preencher ou desgastar discos desnecessariamente e
encerrar processos de forma previsivel. Melhorias devem ser incrementais e
compativeis com o fluxo atual de Python, Tkinter, go2rtc e FFmpeg.

## Enriquecimento dos pedidos

O usuario pode escrever pedidos curtos ou informais. Antes de agir, transforme
internamente o pedido em um briefing com:

1. Objetivo real e resultado esperado.
2. Contexto do projeto e componentes afetados.
3. Riscos para gravacoes, discos, Windows e atualizacao.
4. Restricoes, principalmente "sem quebrar nada".
5. Criterios de aceite e validacoes executaveis.

Nao exija que o usuario conheca termos tecnicos. Descubra no repositorio tudo o
que puder e pergunte apenas quando a resposta nao puder ser inferida com
seguranca. Quando houver ambiguidade relevante, informe em uma frase qual foi o
pedido interpretado antes de editar.

## Fluxo obrigatorio

1. Leia este arquivo, `README.md`, `git status` e os trechos afetados.
2. Preserve mudancas existentes e nunca reverta trabalho alheio.
3. Mapeie produtores, consumidores, threads e arquivos antes de editar estado
   compartilhado.
4. Prefira mudancas pequenas dentro das funcoes existentes.
5. Valide sintaxe, testes isolados e `git diff --check`.
6. Nao inicie cameras, go2rtc, FFmpeg real ou gravacao real sem necessidade e
   sem avisar o usuario.
7. Relate o que foi validado e o que ainda depende de teste real prolongado.
8. Depois da validacao, crie um commit contendo somente os arquivos da tarefa,
   salvo quando o usuario pedir explicitamente para nao commitar.

## Dependencias, modelos e repositorios externos

- Antes de integrar algo do GitHub, compare pelo menos duas opcoes oficiais
  viaveis. Avalie manutencao, estado de arquivamento, licenca para o uso real,
  seguranca, compatibilidade com Windows e hardware, consumo, portabilidade,
  complexidade operacional e modos de falha.
- "Melhor" significa a opcao de menor risco que atende ao requisito medido, e
  nao o repositorio com mais estrelas, recursos ou novidade.
- Fixe versao, release ou commit. Para modelos e binarios, fixe tambem tamanho e
  SHA-256, mantenha aviso de licenca e torne a instalacao reproduzivel.
- Dependencias opcionais de analise devem falhar em modo degradado. Gravacao,
  sincronizacao, armazenamento e encerramento nao podem depender delas.
- Nao acompanhe `main`, nao substitua dependencias automaticamente em operacao e
  nao baixe codigo executavel no caminho 24h. Atualize deliberadamente somente
  depois de revisar licenca, seguranca e mudancas, testar em diretorio temporario
  e comparar CPU, memoria, disco e resultados com a versao anterior.

## Protocolo de teste real

Quando a mudanca afetar gravacao, streams, go2rtc, encerramento, energia ou
armazenamento, prefira um ensaio real controlado depois dos testes isolados:

1. Execute `--health-check` e registre espaco de C e do HD, processos e total de
   relatorios `Kernel_144` nas ultimas 24 horas.
2. Confirme que nao existe outra instancia do NVR antes do ensaio.
3. Use `--smoke-test-seconds 180`, ou outro limite entre 30 e 1800 segundos.
4. Durante o ensaio, confirme os nomes dos streams sem expor URLs ou
   credenciais, crescimento dos temporarios, CPU, memoria e threads.
5. Interrompa com `--safe-stop` se o HD desconectar, surgir novo `Kernel_144`,
   a memoria do processo passar de 750 MB ou a gravacao parar de crescer.
6. Ao final, confirme que nao sobraram processos do ensaio, arquivos de trava
   ou artefatos `.finalizing`, `.syncing` e `.recovering`.
7. Valide os arquivos novos sem mover, apagar, colocar em quarentena ou rodar o
   scanner sobre o acervo real.
8. Compare o diagnostico final com a linha de base e relate qualquer piora.

Nao use um ensaio real para substituir testes isolados. Para mudancas apenas em
documentacao ou funcoes puras, ele nao e necessario. Teste de 24 horas exige
janela supervisionada e nao deve comecar enquanto os eventos USBXHCI ainda
estiverem aumentando.

## Regras de seguranca das gravacoes

- Nunca apague video nao vazio que ainda nao tenha uma copia validada.
- Nunca considere apenas o tamanho como prova de que dois videos sao iguais.
- Publique arquivos por temporario no destino, `fsync` e troca atomica.
- Preserve temporarios quando houver erro, timeout, desligamento ou destino
  indisponivel.
- Nao sobrescreva arquivos de mesmo nome com conteudo diferente; gere um nome
  alternativo.
- Fallback local deve respeitar reserva minima de espaco e nunca excluir
  material pendente para cumprir um limite arbitrario.
- Scanner deve ser serial, incremental e limitado. Timeout e inconclusivo, nao
  prova de corrupcao. Quarentena exige confirmacao e fica no mesmo disco.
- Encerramento deve parar novas tarefas, fechar conexoes, aguardar gravadores e
  somente depois encerrar go2rtc e Tkinter.
- Nunca escolha uma unidade apenas por ser `D:`. Use configuracao explicita,
  pasta existente ou identidade validada do volume.
- A limpeza emergencial deve ficar desativada por padrao e, quando habilitada,
  nunca pode remover gravacoes mais novas que a retencao aprovada.
- Testes de armazenamento usam somente diretorios temporarios. Nunca testam
  exclusao, rotacao ou quarentena sobre gravacoes reais.

## Regras da inteligencia operacional

- A inteligencia e local e deterministica. Nao enviar imagens, credenciais,
  URLs de camera ou logs para servicos externos.
- Separar sintoma, correlacao e causa provavel. Uma inferencia nunca deve ser
  apresentada como certeza sem sinais diretos.
- Historico `Kernel_144` e falha nova na sessao sao situacoes diferentes.
- Toda nova regra exige teste de cenario positivo e teste contra falso alerta.
- A inteligencia pode adiar manutencao automatica pesada, mas nao pode apagar
  videos, mudar retencao ou desligar gravacoes apenas por inferencia.
- Logs de inteligencia devem ocorrer por transicao de conclusao, nao em cada
  coleta. Tendencias em memoria devem ter janela e quantidade limitadas.
- O snapshot JSON continua sendo a fonte de evidencia; a interface apenas
  apresenta a mesma conclusao de forma resumida.

## Acoes que exigem cuidado adicional

- Exclusao ou mudanca da retencao de videos.
- Alteracao do Windows, energia USB, tarefas agendadas ou firewall.
- Instalacao ou atualizacao de executaveis e dependencias.
- Execucao de `taskkill`, desligamento, gravacao ou scanner sobre o acervo real.
- Mudanca de credenciais ou identificadores das cameras. Nao exponha segredos
  em logs, testes, respostas ou commits.
- Configuracoes geradas pelo go2rtc, segredos locais, hashes de atualizacao e
  a pasta web publicada nunca entram no Git. A API deve expor somente as rotas
  necessarias, com autenticacao para clientes da rede.

## Contexto operacional conhecido

- O aplicativo e usado principalmente em modo silencioso e deve operar 24h.
- Ha historico de `LiveKernelEvent 144` ligado a `USBXHCI`; isso pode indicar
  controlador, driver, cabo, energia ou dispositivo USB, e nao apenas o Python.
- O arquivo principal e grande e concentra GUI, gravacao, sincronizacao,
  manutencao e atualizacao. Refatoracoes devem ser graduais e protegidas por
  testes antes de separar modulos.
- O fluxo direto `/api/stream.ts?src=NOME` deve ser preservado, sem adicionar
  transcodificacao continua ou dependencias pesadas.
- As regras de firewall atuais sao uma decisao operacional do responsavel. Nao
  amplie portas ou perfis sem pedido explicito; endureca primeiro o proprio
  servico e suas credenciais.

## Definicao de pronto

Uma mudanca so esta pronta quando preserva a origem em todas as falhas
simuladas, nao usa o HD real nos testes, passa pela compilacao Python, pelos
testes de regressao e pela revisao final do diff. Para operacao 24h, deixe claro
quando ainda falta teste real de duracao, queda de energia ou desconexao USB.
