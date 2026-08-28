# Pesquisa de reconhecimento facial local

Data: 23/08/2026

## Objetivo

Revisar repositorios oficiais para reconhecimento de perfis consentidos no NVR
Windows, priorizando operacao local, licenca adequada, baixo impacto em CPU e
memoria, instalacao reproduzivel e falha isolada da gravacao.

## Opcoes avaliadas

- OpenCV Zoo YuNet + SFace: escolhido e ja integrado. Reutiliza OpenCV DNN em
  CPU, possui modelos pequenos, API nativa do OpenCV e licencas permissivas por
  modelo. O projeto fixa commit, tamanho e SHA-256, sem atualizar por `main`.
- DeepFace: codigo MIT e muitos modelos, mas o conjunto padrao adiciona
  TensorFlow, Keras, Flask, Pandas e outros pacotes. As licencas dos modelos
  encapsulados continuam sendo responsabilidade de cada modelo. E peso e
  superficie de falha desnecessarios para duas cameras em um NVR 24h.
- `face_recognition`: API simples e codigo MIT, mas depende de dlib/CMake e o
  proprio repositorio nao oferece suporte oficial ao Windows. Aumentaria o risco
  de instalacao e portabilidade neste ambiente.
- InsightFace: codigo MIT e recursos avancados, mas os modelos publicos
  pre-treinados sao limitados a pesquisa nao comercial salvo licenca separada.
  Tambem adicionaria outro runtime ou servico local sem necessidade atual.

## Decisao

Manter YuNet + SFace. O limiar local de similaridade e mais conservador que o
exemplo oficial e ainda exige margem sobre o segundo candidato e duas
confirmacoes consecutivas. Falha do modelo degrada apenas a analise; nao altera
stream, gravacao, retencao ou armazenamento.

Nome, funcao e vetor ficam juntos no payload DPAPI do banco biometrico separado.
A funcao e escolhida manualmente no cadastro consentido. A revisao de 28/08/2026
manteve YuNet + SFace e adicionou agrupamento provisorio local: tres observacoes
compatíveis em quadros distintos podem criar `Pessoa N`, sem inferir nome ou
funcao. O payload DPAPI contem vetor e recorte de revisao, possui limite de 100
itens e expira em 10 dias. Somente a confirmacao humana promove o agrupamento a
perfil. A captura guarda contexto anonimizado, prancha facial cifrada e apenas o
`profile_id`; nomes nao sao duplicados nos arquivos.

No teste local com recorte do preview real, o limiar YuNet `0.90` encontrou zero
rostos e `0.80` encontrou um. O padrao foi ajustado para `0.80`, sem aumentar a
entrada maxima de `960x540`, o teto de oito rostos ou a frequencia da analise.

Nenhum novo repositorio ou runtime foi adicionado. O OpenCV ja fixado atende a
comparacao vetorial, reduzindo CPU, superficie de falha e risco operacional em
relacao a DeepFace, dlib/`face_recognition` ou InsightFace.

## Proximas validacoes

- Calibrar falso aceite e falsa rejeicao com perfis consentidos e iluminacao real.
- Medir CPU, memoria e estabilidade em ensaio supervisionado prolongado.
- Avaliar prova de presenca somente se houver necessidade real e modelo com
  licenca, hash e fallback aprovados; fotografia ou video ainda pode enganar o
  reconhecimento atual.

## Fontes primarias

- https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
- https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface
- https://github.com/serengil/deepface
- https://github.com/serengil/deepface/blob/master/requirements.txt
- https://github.com/ageitgey/face_recognition
- https://github.com/deepinsight/insightface
- https://github.com/deepinsight/insightface/blob/master/server/LICENSING.md
