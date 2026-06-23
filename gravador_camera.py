import time
import os
import sys
import argparse
import socket
import json
import urllib.request
from datetime import datetime, timedelta

# ================= ARGUMENTOS DA LINHA DE COMANDO =================
parser = argparse.ArgumentParser(description="Gravador de Câmeras RTSP via HTTP Remux")
parser.add_argument("--stream", type=str, default="farmacia", help="Nome da stream no go2rtc (Ex: farmacia)")
parser.add_argument("--dir", type=str, default="G:/Meu Drive/CAMERAS/CAMERA 1 FARMACIA", help="Diretório de salvamento do Drive")
parser.add_argument("--lock", type=str, default="gravando.lock", help="Nome do arquivo de lock de controle")
parser.add_argument("--log", type=str, default="erros_gravador.log", help="Nome do arquivo de log")
args = parser.parse_args()

PASTA_GRAVACOES = args.dir

# Arquivos locais de controle
PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
LOCK_FILE = os.path.join(PROJ_DIR, args.lock)
LOG_FILE = os.path.join(PROJ_DIR, args.log)
# ==================================================================

def escrever_log(mensagem):
    """Escreve uma linha no arquivo de log com carimbo de data/hora"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] [{args.stream.upper()}] {mensagem}\n"
    print(formatted.strip()) # Imprime no console (caso rodando em modo interativo)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted)
    except Exception:
        pass

def criar_lock_file():
    """Cria o arquivo de lock contendo o PID do processo atual"""
    try:
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        escrever_log(f"Erro ao criar lock file: {str(e)}")

def verificar_lock_file():
    """Retorna True se o lock file ainda existe, False caso contrário"""
    return os.path.exists(LOCK_FILE)

# Tenta criar a pasta de gravações primária e testa a escrita (para detectar erros de sincronização/Drive)
try:
    os.makedirs(PASTA_GRAVACOES, exist_ok=True)
    teste_path = os.path.join(PASTA_GRAVACOES, ".teste_escrita")
    with open(teste_path, "w") as f:
        f.write("teste")
    os.remove(teste_path)
    pasta_final = PASTA_GRAVACOES
except Exception as e:
    pasta_fallback = os.path.join(PROJ_DIR, "backup_gravacoes", args.stream)
    os.makedirs(pasta_fallback, exist_ok=True)
    pasta_final = pasta_fallback
    escrever_log(f"AVISO: Pasta do Drive indisponivel ({str(e)}). Usando backup local: {pasta_fallback}")

def obter_faixa_horario(dt):
    """Calcula a faixa de 30 minutos exata do relógio para o horário fornecido"""
    if dt.minute < 30:
        inicio = dt.replace(minute=0, second=0, microsecond=0)
        fim = dt.replace(minute=30, second=0, microsecond=0)
    else:
        inicio = dt.replace(minute=30, second=0, microsecond=0)
        fim = (dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return inicio, fim

def obter_ip_local():
    """Obtém o IP local de forma robusta"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "127.0.0.1"
    return ip

def verificar_duplicidade_rede():
    """Retorna o nome do PC e o IP se outro gravador estiver ativo no Google Drive"""
    gdrive_root = os.path.dirname(PASTA_GRAVACOES)
    lock_name = f".active_recorder_{args.stream}.json"
    lock_path = os.path.join(gdrive_root, lock_name)
    
    if not os.path.exists(lock_path):
        return None
        
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        last_heartbeat = data.get("timestamp", 0)
        hostname = data.get("hostname", "")
        ip = data.get("ip", "")
        
        current_time = time.time()
        my_hostname = socket.gethostname()
        
        # Se o batimento foi atualizado há menos de 90 segundos por outro PC
        if (current_time - last_heartbeat < 90) and (hostname != my_hostname):
            return {"hostname": hostname, "ip": ip}
    except Exception:
        pass
    return None

def atualizar_heartbeat():
    """Escreve/atualiza o arquivo de batimento cardíaco no Drive"""
    gdrive_root = os.path.dirname(PASTA_GRAVACOES)
    if not os.path.exists(gdrive_root):
        return
        
    lock_name = f".active_recorder_{args.stream}.json"
    lock_path = os.path.join(gdrive_root, lock_name)
    
    data = {
        "timestamp": time.time(),
        "hostname": socket.gethostname(),
        "ip": obter_ip_local()
    }
    
    try:
        with open(lock_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def gravar_bloco(stream_name, pasta_final):
    # Calcula os horários de início e fim alinhados com o relógio
    agora = datetime.now()
    inicio_bloco, fim_bloco = obter_faixa_horario(agora)
    
    data_dia = inicio_bloco.strftime("%Y-%m-%d")
    hora_inicio = inicio_bloco.strftime("%H-%M")
    hora_fim = fim_bloco.strftime("%H-%M")
    
    nome_arquivo = os.path.join(pasta_final, f"camera_{data_dia}_{hora_inicio}_ate_{hora_fim}.mp4")
    escrever_log(f"Iniciando gravacao do bloco: {os.path.basename(nome_arquivo)}")
    
    url = f"http://127.0.0.1:1984/api/stream.mp4?src={stream_name}"
    
    atualizar_heartbeat()
    last_heartbeat_time = time.time()
    
    try:
        req = urllib.request.Request(url)
        # Timeout curto para detecção de queda rápida
        with urllib.request.urlopen(req, timeout=15) as response:
            with open(nome_arquivo, "wb") as out_file:
                while True:
                    # Verifica se o lock file foi removido (sinal de parada da GUI)
                    if not verificar_lock_file():
                        escrever_log("Sinal de parada detectado (lock foi removido).")
                        return "parar"
                        
                    # Verifica duplicidade na rede e atualiza heartbeat a cada 30 segundos
                    agora_ts = time.time()
                    if agora_ts - last_heartbeat_time >= 30:
                        conflito = verificar_duplicidade_rede()
                        if conflito:
                            escrever_log(f"[ERRO_DUPLICADO] O computador {conflito['hostname']} ({conflito['ip']}) ja esta gravando esta camera.")
                            return "duplicado"
                        atualizar_heartbeat()
                        last_heartbeat_time = agora_ts
                        
                    # Verifica se já passamos do fim do bloco de 30 minutos
                    if datetime.now() >= fim_bloco:
                        escrever_log("Bloco anterior finalizado e salvo no Drive.")
                        return "rotacionar"
                        
                    # Lê os dados da stream em blocos
                    try:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            escrever_log("Fim da stream detectado (conexao fechada pelo go2rtc).")
                            break
                        out_file.write(chunk)
                    except socket.timeout:
                        # Em caso de timeout de socket (sem dados temporários), continua tentando
                        continue
    except Exception as e:
        escrever_log(f"Erro na conexao com a stream: {str(e)}")
        # Se der erro de conexão e o arquivo estiver vazio, remove-o
        if os.path.exists(nome_arquivo) and os.path.getsize(nome_arquivo) == 0:
            try:
                os.remove(nome_arquivo)
            except Exception:
                pass
        return "erro"
        
    return "reconectar"

if __name__ == "__main__":
    criar_lock_file()
    
    escrever_log("=== INICIANDO SERVICO DE GRAVACAO DA CAMERA (HTTP REMUX) ===")
    
    try:
        while verificar_lock_file():
            # Verifica duplicidade na rede antes de tentar conectar
            conflito = verificar_duplicidade_rede()
            if conflito:
                escrever_log(f"[ERRO_DUPLICADO] O computador {conflito['hostname']} ({conflito['ip']}) ja esta gravando esta camera.")
                break
                
            status = gravar_bloco(args.stream, pasta_final)
            
            if status == "parar" or status == "duplicado":
                break
                
            if status == "erro" or status == "reconectar":
                escrever_log("Aguardando 10 segundos antes de tentar reconectar...")
                time.sleep(10)
            elif status == "rotacionar":
                time.sleep(1)
    except KeyboardInterrupt:
        escrever_log("Gravacao encerrada manualmente via console.")
    finally:
        # Garante a remoção do lock file ao fechar o script
        if os.path.exists(LOCK_FILE):
            try:
                os.remove(LOCK_FILE)
            except Exception:
                pass
                
        # Limpa o arquivo de batimento cardíaco se fomos nós que o criamos
        try:
            gdrive_root = os.path.dirname(PASTA_GRAVACOES)
            lock_name = f".active_recorder_{args.stream}.json"
            lock_path = os.path.join(gdrive_root, lock_name)
            if os.path.exists(lock_path):
                with open(lock_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("hostname") == socket.gethostname():
                    os.remove(lock_path)
        except Exception:
            pass
            
        escrever_log("=== SERVICO DE GRAVACAO ENCERRADO ===")
