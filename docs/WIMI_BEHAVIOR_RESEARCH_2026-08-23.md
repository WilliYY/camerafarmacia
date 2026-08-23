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

## Limites

- Contagem e estimativa, sujeita a falso positivo, falso negativo e oclusao.
- Rostos detectados recebem achatamento adicional; um rosto nao detectado fica
  protegido apenas pela pixelizacao global, pois nenhum detector e infalivel.
- Permanencia pertence ao campo de visao da camera, nao a uma identidade.
- Nao ha tracking individual, zonas, fila, emocao, intencao ou produtividade.
- Antes de adicionar ByteTrack, e necessario medir CPU/memoria por 24 horas e
  calibrar zonas com imagens anonimizadas de cada camera.

## Validacao desta entrega

- A suite completa passou `195` testes, incluindo falso positivo isolado,
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
