import cv2
import time
import os
from datetime import datetime, timedelta

# ================= CONFIGURAÇÕES =================
# O go2rtc está rodando localmente na porta 8554 e transmitindo o canal "farmacia"
RTSP_URL = "rtsp://localhost:8554/farmacia"

# Pasta no seu Google Drive (G:) configurada para salvar os vídeos
PASTA_GRAVACOES = "G:/Meu Drive/CAMERAS/CAMERA 1 FARMACIA"

# Resolução HD forçada para economizar armazenamento
LARGURA_HD = 1280
ALTURA_HD = 720
# =================================================

os.makedirs(PASTA_GRAVACOES, exist_ok=True)

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
    print(f"\n[+] Conectando ao stream local do go2rtc em: {RTSP_URL}...")
    cap = cv2.VideoCapture(RTSP_URL)
    
    if not cap.isOpened():
        print("[!] Erro: Não foi possível conectar ao go2rtc. Verifique se o go2rtc.exe está rodando.")
        return False

    # Taxa de quadros (FPS)
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0 or fps > 60:
        fps = 15.0  # Padrão da câmera
        
    print(f"[+] Conectado com sucesso ao go2rtc!")
    print(f"[+] Vídeo original será redimensionado para HD: {LARGURA_HD}x{ALTURA_HD} | FPS: {fps}")
    print(f"[+] Gravando em: {PASTA_GRAVACOES}")
    
    # Codec de vídeo MP4
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = None
    bloco_atual_inicio = None
    
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("[!] Sinal de vídeo perdido ou oscilando. Tentando reconectar...")
                break
            
            # Redimensiona o frame para HD (1280x720) para economizar espaço
            frame_hd = cv2.resize(frame, (LARGURA_HD, ALTURA_HD))
            
            agora = datetime.now()
            inicio_bloco, fim_bloco = obter_faixa_horario(agora)
            
            # Verifica se o bloco de horário mudou
            if out is None or bloco_atual_inicio != inicio_bloco:
                if out is not None:
                    out.release()
                    print(f"[+] Bloco anterior finalizado e salvo no Google Drive.")
                
                bloco_atual_inicio = inicio_bloco
                
                data_dia = inicio_bloco.strftime("%Y-%m-%d")
                hora_inicio = inicio_bloco.strftime("%H-%M")
                hora_fim = fim_bloco.strftime("%H-%M")
                
                nome_arquivo = os.path.join(PASTA_GRAVACOES, f"camera_{data_dia}_{hora_inicio}_ate_{hora_fim}.mp4")
                print(f"[+] Criando novo bloco exato: {nome_arquivo}")
                
                out = cv2.VideoWriter(nome_arquivo, fourcc, fps, (LARGURA_HD, ALTURA_HD))
            
            out.write(frame_hd)
            
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
                
    except KeyboardInterrupt:
        print("\n[!] Gravação encerrada manualmente pelo usuário.")
    finally:
        if cap is not None:
            cap.release()
        if out is not None:
            out.release()
        cv2.destroyAllWindows()
    return True

if __name__ == "__main__":
    while True:
        sucesso = gravar_fluxo()
        if not sucesso:
            print("[!] Aguardando 10 segundos antes de tentar reconectar...")
            time.sleep(10)
        else:
            time.sleep(2)
