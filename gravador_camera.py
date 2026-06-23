import cv2
import time
import os
import sys
import numpy as np
from datetime import datetime, timedelta

# ================= CONFIGURAÇÕES =================
RTSP_URL = "rtsp://localhost:8554/farmacia"
PASTA_GRAVACOES = "G:/Meu Drive/CAMERAS/CAMERA 1 FARMACIA"
LARGURA_HD = 1280
ALTURA_HD = 720

# Arquivos locais de controle
PROJ_DIR = r"C:\Users\Thiesen\Desktop\camera farmacia"
LOCK_FILE = os.path.join(PROJ_DIR, "gravando.lock")
LOG_FILE = os.path.join(PROJ_DIR, "erros_gravador.log")
# =================================================

os.makedirs(PASTA_GRAVACOES, exist_ok=True)

def escrever_log(mensagem):
    """Escreve uma linha no arquivo erros_gravador.log com carimbo de data/hora"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {mensagem}\n"
    print(formatted.strip()) # Imprime no console (caso rodando em modo interativo)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted)
    except Exception:
        pass

def criar_lock_file():
    """Cria o arquivo gravando.lock contendo o PID do processo atual"""
    try:
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        escrever_log(f"Erro ao criar lock file: {str(e)}")

def verificar_lock_file():
    """Retorna True se o lock file ainda existe, False caso contrário"""
    return os.path.exists(LOCK_FILE)

def obter_faixa_horario(dt):
    """Calcula a faixa de 30 minutos exata do relógio para o horário fornecido"""
    if dt.minute < 30:
        inicio = dt.replace(minute=0, second=0, microsecond=0)
        fim = dt.replace(minute=30, second=0, microsecond=0)
    else:
        inicio = dt.replace(minute=30, second=0, microsecond=0)
        fim = (dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return inicio, fim

def gravar_fluxo():
    escrever_log(f"Conectando ao stream do go2rtc em: {RTSP_URL}...")
    
    # Inicia captura de vídeo
    cap = cv2.VideoCapture(RTSP_URL)
    
    # Configura timeouts de conexão e leitura do FFmpeg no OpenCV para evitar hangs
    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
    
    if not cap.isOpened():
        escrever_log("Erro: Não foi possível conectar ao go2rtc local. Ponte RTSP desligada?")
        return False

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0 or fps > 60:
        fps = 15.0  # FPS padrão da câmera Positivo
        
    escrever_log(f"Conectado! Resolução interna de gravação: {LARGURA_HD}x{ALTURA_HD} | FPS: {fps}")
    
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = None
    bloco_atual_inicio = None
    
    # Variáveis para detecção de congelamento
    prev_frame = None
    quadros_identicos_consecutivos = 0
    limite_quadros_congelados = int(fps * 15)  # 15 segundos de congelamento
    
    try:
        while True:
            # 1. Verifica se o painel GUI enviou comando para parar deletando o arquivo lock
            if not verificar_lock_file():
                escrever_log("Sinal de parada detectado (gravando.lock foi removido). Encerrando gravação...")
                break
                
            ret, frame = cap.read()
            if not ret:
                escrever_log("Erro: Sinal de vídeo perdido ou oscilando. Tentando reconectar...")
                break
            
            # 2. Detector de Congelamento de Imagem
            if prev_frame is not None:
                # Compara rapidamente se os quadros são idênticos em nível de bytes (muito leve)
                if np.array_equal(frame, prev_frame):
                    quadros_identicos_consecutivos += 1
                    if quadros_identicos_consecutivos >= limite_quadros_congelados:
                        escrever_log("Aviso: Travamento de imagem detectado (frames idênticos por 15s). Forçando reconexão...")
                        break
                else:
                    quadros_identicos_consecutivos = 0
                    prev_frame = frame.copy()
            else:
                prev_frame = frame.copy()
            
            # Redimensiona o quadro para HD (1280x720) para economizar espaço em disco
            frame_hd = cv2.resize(frame, (LARGURA_HD, ALTURA_HD))
            
            agora = datetime.now()
            inicio_bloco, fim_bloco = obter_faixa_horario(agora)
            
            # 3. Lógica de Blocos Exatos de 30 Minutos (Sincronizados com o relógio)
            if out is None or bloco_atual_inicio != inicio_bloco:
                if out is not None:
                    out.release()
                    escrever_log("Bloco anterior finalizado e salvo no Drive.")
                
                bloco_atual_inicio = inicio_bloco
                
                data_dia = inicio_bloco.strftime("%Y-%m-%d")
                hora_inicio = inicio_bloco.strftime("%H-%M")
                hora_fim = fim_bloco.strftime("%H-%M")
                
                nome_arquivo = os.path.join(PASTA_GRAVACOES, f"camera_{data_dia}_{hora_inicio}_ate_{hora_fim}.mp4")
                escrever_log(f"Iniciando gravação do bloco: {os.path.basename(nome_arquivo)}")
                
                out = cv2.VideoWriter(nome_arquivo, fourcc, fps, (LARGURA_HD, ALTURA_HD))
            
            out.write(frame_hd)
            
            # Pequeno delay para aliviar uso de CPU entre frames
            # O VideoCapture.read() já aguarda o tempo natural da stream, mas um waitKey opcional ajuda
            if cv2.waitKey(1) & 0xFF == ord('q'):
                escrever_log("Tecla de encerramento pressionada.")
                break
                
    except Exception as e:
        escrever_log(f"Erro inesperado no loop de gravação: {str(e)}")
    finally:
        # Garante a liberação de recursos de forma segura (NÃO corrompe o vídeo final)
        if cap is not None:
            cap.release()
        if out is not None:
            out.release()
            escrever_log("Fluxo de vídeo fechado e salvo com sucesso.")
        cv2.destroyAllWindows()
        
    return True

if __name__ == "__main__":
    # Garante a presença do lock file ao iniciar o processo
    criar_lock_file()
    
    escrever_log("=== INICIANDO SERVIÇO DE GRAVAÇÃO DA CÂMERA ===")
    
    try:
        while verificar_lock_file():
            sucesso = gravar_fluxo()
            if not verificar_lock_file():
                break
            if not sucesso:
                escrever_log("Aguardando 10 segundos antes de tentar reconectar...")
                time.sleep(10)
            else:
                time.sleep(2)
    except KeyboardInterrupt:
        escrever_log("Gravação encerrada manualmente via console.")
    finally:
        # Garante a remoção do lock file ao fechar o script
        if os.path.exists(LOCK_FILE):
            try:
                os.remove(LOCK_FILE)
            except Exception:
                pass
        escrever_log("=== SERVIÇO DE GRAVAÇÃO ENCERRADO ===")
