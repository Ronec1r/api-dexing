import logging
from flask import Flask, request, jsonify
import requests
from requests.auth import HTTPBasicAuth

"""
Módulo de integração com equipamentos Dexing para monitoramento via Zabbix.
Este script expõe uma API Flask que consulta métricas de sintonizadores (tuners).
"""

app = Flask(__name__)

# --- Configuração de Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("DexinMiddleware")

# --- Configurações Padrão ---
CONFIG = {
    "CHUNK_SIZE": 9,
    "PORT_DEFAULT": 80,
    "USER_DEFAULT": "admin",
    "PASS_DEFAULT": "admin",
    "TIMEOUT": 8
}

@app.route("/health", methods=["GET"])
def health_check():
    """Endpoint de saúde para verificar se a API está rodando."""
    return jsonify({"status": "ok", "message": "API is running"}), 200

def parse_tuner_data(raw_content):
    """
    Função auxiliar para processar a string bruta do Dexing e converter em JSON.
    """
    values = raw_content.split(',')
    zabbix_data = []
    chunk_size = CONFIG["CHUNK_SIZE"]

    for i in range(0, len(values), chunk_size):
        chunk = values[i:i+chunk_size]

        # Validação básica do tamanho do pedaço
        if len(chunk) < chunk_size or not chunk[0].strip():
            continue

        try:
            tuner_id_str = chunk[0]
            tuner_id = int(tuner_id_str)

            # --- TRAVA DE SEGURANÇA (CORREÇÃO DE BUG) ---
            # Ignora IDs impossíveis (acima de 32) para evitar leitura de lixo
            if tuner_id <= 0 or tuner_id > 32:
                continue 
            # --------------------------------------------

            cn_val = float(chunk[6].lower().replace(' db', '').strip())
            pwr_val = float(chunk[7].lower().replace(' dbm', '').strip())
            
            item = {
                "{#TUNER_ID}": tuner_id_str,
                "tuner_id": tuner_id_str,
                "quality": int(chunk[3]) if chunk[3].isdigit() else 0,
                "strength": int(chunk[4]) if chunk[4].isdigit() else 0,
                "cn": cn_val,
                "power": pwr_val,
                "ber": chunk[8].strip()
            }
            zabbix_data.append(item)

        except (ValueError, IndexError) as e:
            logger.debug(f"Erro ao processar chunk {chunk[0]}: {e}")
            continue

    return zabbix_data

@app.route("/metrics", methods=["POST"])
def get_metrics():
    req_data = request.get_json()

    if not req_data:
        logger.warning("Requisição recebida sem payload JSON")
        return jsonify({"error": "Invalid JSON body"}), 400

    target_ip = req_data.get('ip')
    target_port = req_data.get('port', CONFIG["PORT_DEFAULT"])
    user = req_data.get('user', CONFIG["USER_DEFAULT"])
    password = req_data.get('password', CONFIG["PASS_DEFAULT"])

    if not target_ip:
        logger.warning("IP não fornecido na requisição")
        return jsonify({"error": "IP missing"}), 400

    logger.info(f"Iniciando coleta: {target_ip}:{target_port}")

    url = f"http://{target_ip}:{target_port}/cgi-bin/tuner.cgi"
    
    payload = {
        "h_setflag": "3",
        "edit_ch": "1",
        "h_tuner_type": "1"
    }

    try:
        response = requests.post(
            url, 
            data=payload, 
            auth=HTTPBasicAuth(user, password),
            timeout=CONFIG["TIMEOUT"]
        )
        
        if response.status_code != 200:
            logger.error(f"Falha HTTP {response.status_code} em {target_ip}")
            return jsonify({"error": f"HTTP {response.status_code}"}), 502

        raw_data = response.text
        
        if ":" not in raw_data:
            logger.warning(f"Formato de resposta inesperado de {target_ip}")
            return jsonify([]), 200

        content = raw_data.split(':', 1)[1]
        final_data = parse_tuner_data(content)
        
        logger.info(f"Sucesso: {len(final_data)} tuners coletados em {target_ip}")
        return jsonify(final_data)

    except requests.exceptions.Timeout:
        logger.error(f"Timeout ao conectar em {target_ip}")
        return jsonify({"error": "Request timed out"}), 504
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de conexão em {target_ip}: {str(e)}")
        return jsonify({"error": f"Connection error: {str(e)}"}), 502
        
    except Exception as e:
        logger.exception(f"Erro crítico não tratado em {target_ip}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)