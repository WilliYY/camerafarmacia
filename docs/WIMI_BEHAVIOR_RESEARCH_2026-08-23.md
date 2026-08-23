# Pesquisa de visao e comportamento local

Data: 23/08/2026

## Objetivo

Melhorar a nitidez das evidencias anonimizadas e adicionar sinais objetivos de
comportamento por camera sem abrir outro stream, enviar imagens ou interferir na
gravacao.

## Opcoes avaliadas

- OpenCV Zoo NanoDet: escolhido. O modelo quantizado tem 1.123.958 bytes, usa
  OpenCV DNN ja isolado pelo projeto, possui licenca Apache-2.0 e detecta a classe
  `person`. No PC atual, quadro sintetico `1280x720` mediu media aquecida de
  152,9 ms em CPU.
- OpenCV Zoo MP-PersonDet: adequado para deteccao e pontos corporais, mas o
  pos-processamento e maior e os pontos de pose nao sao necessarios nesta fase.
- ByteTrack: referencia madura e MIT para trajetorias multiobjeto. Fica como
  evolucao futura depois de validar detector, angulo das cameras e zonas reais;
  nao e necessario para permanencia agregada por camera.
- Supervision: oferece tracking e zonas, mas adicionaria uma dependencia ampla;
  a propria classe ByteTrack esta em migracao para outro pacote nas versoes
  atuais.
- ONNX Runtime: runtime MIT eficiente, mas duplicaria a camada de inferencia que
  o OpenCV DNN ja fornece neste projeto.
- Ultralytics YOLO: conjunto amplo e ativo, mas requer PyTorch no pacote Python
  e usa AGPL-3.0 ou licenca Enterprise. Aumentaria instalacao, consumo e
  obrigacoes sem necessidade para a contagem agregada atual.
- OpenVINO: runtime Apache-2.0 forte para CPU, GPU Intel e NPU. Continua como
  candidato de otimizacao, mas so deve substituir o OpenCV DNN se um benchmark
  no PC do NVR demonstrar ganho relevante e estabilidade equivalente.
- MMAction2: referencia Apache-2.0 para reconhecimento temporal de acoes, mas
  adiciona PyTorch, MMEngine e MMCV, alem de exigir um modelo adequado ao angulo
  real das cameras. Nao foi incorporado: o custo operacional e alto e seus
  modelos genericos nao comprovam com seguranca uma acao especifica como uso de
  celular neste ambiente.

## Parecer de selecao

Para o requisito atual, OpenCV Zoo NanoDet continua sendo o melhor encaixe, nao
o detector universalmente mais preciso. Ele reutiliza o runtime ja isolado,
mantem o modelo quantizado em 1.123.958 bytes e possui licenca permissiva. O
proprio resultado oficial informa AP50 de 67,5 para `person` e desempenho menor
em objetos pequenos; por isso a deteccao e uma metrica auxiliar, nunca prova de
identidade, produtividade ou seguranca.

A integracao fixa o commit do OpenCV Zoo, tamanho e SHA-256. Falha, ausencia ou
incompatibilidade do modelo degrada somente a metrica de pessoas e nao bloqueia
gravacao, painel, movimento ou reconhecimento facial. Nao existe atualizacao
automatica a partir de `main`.

ByteTrack passa a ser considerado somente quando houver necessidade validada de
trajetorias individuais. ONNX Runtime ou OpenVINO entram apenas depois de
benchmark comparativo no hardware real. Ultralytics nao deve ser incorporado
sem uma decisao explicita de licenca e um teste de carga prolongado.

## Decisao implementada

1. NanoDet quantizado e verificado por tamanho e SHA-256 no instalador existente.
2. Inferencia local em CPU, no mesmo quadro do preview, a cada cinco segundos.
3. Contagem estabilizada de pessoas, pico, inicio/fim de presenca, permanencia
   observada e nivel atual de variacao visual agregado por camera.
4. Duas amostras positivas iniciam e duas ausentes encerram presenca. Falta de
   amostra nunca equivale a ausencia; apos 15 segundos sem confirmacao o painel
   mostra estado desconhecido e congela a duracao confirmada.
5. Modelo ausente ou erro de inferencia degrada somente a metrica de pessoas;
   movimento, rostos, painel e gravacao continuam.
6. Evidencias novas usam ate `1280x720`, JPEG 82, pixelizacao global equilibrada
   em blocos de 12 pixels e
   achatamento adicional de cada rosto detectado. Arquivos antigos permanecem
   inalterados.
7. A aba Evidencias usa cards responsivos e paginacao de 24 miniaturas. Selecao
   individual, marcar/desmarcar tudo e exclusao em lote reutilizam o arquivo
   cifrado existente. A aba Comportamento e seu historico nao sao reiniciados.

## Limites

- Contagem e estimativa, sujeita a falso positivo, falso negativo e oclusao.
- Rostos detectados recebem achatamento adicional; um rosto nao detectado fica
  protegido apenas pela pixelizacao global, pois nenhum detector e infalivel.
- Permanencia pertence ao campo de visao da camera, nao a uma identidade.
- Nao ha tracking individual, zonas, fila, emocao, intencao ou produtividade.
- Nao ha detector de uso de celular. Horario e camera de um perfil consentido
  sao fatos observados; qualquer acao corporal futura deve aparecer como
  `possivel evento`, com confianca e revisao humana, nunca como certeza.
- Antes de adicionar ByteTrack, e necessario medir CPU/memoria por 24 horas e
  calibrar zonas com imagens anonimizadas de cada camera.

## Validacao desta entrega

- A suite completa passou `205` testes, incluindo renomeacao retrospectiva de
  perfil, falso positivo isolado,
  oscilacao de contagem, expiracao para estado desconhecido, encerramento seguro,
  anonimizacao defensiva e redimensionamento do preview.
- O manifesto e os modelos locais passaram na verificacao de tamanho e SHA-256.
- Em CPU, o detector teve media aquecida de 152,9 ms em quadro sintetico
  `1280x720` e 177,7 ms em um quadro real `2304x1296` da camera `farmacia2`.
- A entrada RGB do Pillow preserva a mesma ordem usada pelo demo oficial, que
  converte os quadros OpenCV de BGR para RGB antes da inferencia. A validacao
  com `basketball1.png`, imagem de exemplo do repositorio OpenCV, confirmou duas
  pessoas com confiancas 0,834 e 0,693 em 170,6 ms na CPU.
- Um ensaio real controlado de 60 segundos colocou as duas cameras Online e
  produziu dois arquivos TS finais, ambos decodificados pelo FFmpeg com retorno
  zero e sem erro de decodificacao.
- O HD permaneceu disponivel, nao surgiu novo `Kernel_144` e nao restaram
  processos, travas nem arquivos `.finalizing`, `.syncing` ou `.recovering`.
- O FFmpeg exibiu avisos de DTS nao monotono ao enviar os quadros decodificados
  ao muxer nulo. Isso nao impediu a decodificacao, mas fica registrado para uma
  auditoria futura de timestamps antes de afirmar estabilidade continua.
- Persistencia e transicoes de comportamento foram validadas com banco SQLite
  temporario; a coleta integrada de longa duracao ainda depende de ensaio
  supervisionado.

## Fontes primarias

- https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_nanodet
- https://github.com/opencv/opencv_zoo
- https://github.com/FoundationVision/ByteTrack
- https://github.com/roboflow/supervision
- https://github.com/microsoft/onnxruntime
- https://github.com/ultralytics/ultralytics
- https://github.com/openvinotoolkit/openvino
- https://github.com/open-mmlab/mmaction2
