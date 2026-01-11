#!/usr/bin/env python3
# ---
# File: monitor_semaforo_TCS.py
# Directory: [root]
# Ultima Modifica: 2026-01-11
# Versione: 1.29 (No Override)
# ---

"""
MONITOR SEMAFORO - Versione TCS34725 (4 Stati)

V 1.29:
- RIMOSSA FORZATURA BUFFER: Lo script ora rispetta fedelmente
  il valore 'buffer_size' nel file di configurazione, anche se basso.
  Non sovrascrive più automaticamente a 100.
- Restano attive le logiche di stabilità:
  1. Loop a 0.1s (10Hz).
  2. Filtro Rosso a 15 campioni.
  3. Soglie Luminosità differenziate (Base 50, Rosso 100).
"""

import time
import json
import sys
import os
from collections import deque
from datetime import datetime

try:
    import paho.mqtt.client as mqtt
    import board
    import busio
    import adafruit_tcs34725
except ImportError:
    print("❌ Errore: Librerie richieste non trovate.")
    print("   Assicurati di averle installate da 'requirements.txt'")
    sys.exit(1)

# --- CONFIGURAZIONE LOGICA DI RILEVAMENTO ---
CAMPIONI_PER_LETTURA = 1
# Manteniamo il loop rilassato a 0.1s per stabilità
LOOP_SLEEP_TIME = 0.1
STATE_PERSISTENCE_SECONDS = 0.5

# --- SOGLIE LUMINOSITÀ (V 1.27) ---
MIN_LUMINOSITY_BASE = 50
MIN_LUMINOSITY_RED = 100

# --- CONFIGURAZIONE DEBUG LOGGING ---
MAX_DEBUG_LINES = 5000
DEBUG_LOGGING_ENABLED = False

# --- CONFIGURAZIONE MQTT ---
MQTT_BROKER = "192.168.20.163"
MQTT_PORT = 1883
MQTT_USERNAME = "shima"
MQTT_PASSWORD = "shima"
MQTT_TRIGGER_TOPIC = "bma/cambiostato"

# --- PERCORSI FILE ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "config")
CALIBRATION_FILE = os.path.join(CONFIG_DIR, "calibrazione.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "LOG")

is_mqtt_connected = False
current_log_file_path = None
current_log_line_count = 0
CSV_HEADER = "Timestamp,R,G,B,StatoIstantaneo,StatoComposito\n"
STEADY_STATE_THRESHOLD = 0.90


# --- Inizializzazione Hardware ---

def inizializza_sensore(integration_time, gain):
    print("🔧 Inizializzazione sensore TCS34725...")
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        sensor = adafruit_tcs34725.TCS34725(i2c)
        sensor.integration_time = integration_time
        if gain in [1, 4, 16, 60]:
            sensor.gain = gain
        else:
            sensor.gain = 4
        print(f"✅ Sensore inizializzato (Time: {integration_time}ms, Gain: {sensor.gain}x).")
        return sensor
    except Exception as e:
        print(f"❌ ERRORE: Impossibile trovare il sensore TCS34725. {e}")
        return None


def carica_calibrazione():
    global DEBUG_LOGGING_ENABLED, STEADY_STATE_THRESHOLD
    if not os.path.exists(CALIBRATION_FILE):
        print(f"❌ ERRORE: File {CALIBRATION_FILE} non trovato!")
        return None
    try:
        with open(CALIBRATION_FILE, 'r') as f:
            data = json.load(f)
        if not all(k in data for k in ["verde", "non_verde", "buio"]):
            print("❌ ERRORE: Calibrazione incompleta.")
            return None
        DEBUG_LOGGING_ENABLED = data.get('debug_logging', False)
        soglia_percent = data.get('steady_state_threshold', 90)
        STEADY_STATE_THRESHOLD = soglia_percent / 100.0
        return data
    except Exception as e:
        print(f"❌ ERRORE lettura JSON: {e}")
        return None


# --- Funzioni di Lettura ---

def leggi_rgb_attuale(sens):
    try:
        r = sens.color_rgb_bytes
        if len(r) >= 3: return r[:3]
    except:
        pass
    try:
        raw = sens.color_raw
        return min(255, int(raw[0] / 256)), min(255, int(raw[1] / 256)), min(255, int(raw[2] / 256))
    except:
        return 0, 0, 0


def leggi_rgb_stabilizzato(sensor, campioni=CAMPIONI_PER_LETTURA):
    tot_r, tot_g, tot_b, validi = 0, 0, 0, 0
    for _ in range(campioni):
        try:
            r, g, b = leggi_rgb_attuale(sensor)
            if r | g | b == 0: continue
            validi += 1;
            tot_r += r;
            tot_g += g;
            tot_b += b
        except:
            pass
        time.sleep(0.01)
    if validi == 0: return {"R": 0, "G": 0, "B": 0}
    return {"R": int(tot_r / validi), "G": int(tot_g / validi), "B": int(tot_b / validi)}


def calcola_distanza_rgb(rgb1, rgb2):
    return ((rgb1['R'] - rgb2['R']) ** 2 + (rgb1['G'] - rgb2['G']) ** 2 + (rgb1['B'] - rgb2['B']) ** 2) ** 0.5


def get_instant_status(sensor, calib_data):
    rgb = leggi_rgb_stabilizzato(sensor)
    if rgb["R"] == 0 and rgb["G"] == 0 and rgb["B"] == 0: return None, rgb

    # Logica V 1.27
    somma_lux = sum(rgb.values())
    if somma_lux < MIN_LUMINOSITY_BASE: return "SPENTO", rgb

    distanze = {
        "VERDE": calcola_distanza_rgb(rgb, calib_data['verde']),
        "ROSSO": calcola_distanza_rgb(rgb, calib_data['non_verde']),
        "SPENTO": calcola_distanza_rgb(rgb, calib_data['buio'])
    }
    stato = min(distanze, key=distanze.get)

    if stato == "ROSSO" and somma_lux < MIN_LUMINOSITY_RED: return "SPENTO", rgb
    return stato, rgb


def analyze_state_buffer(buffer):
    rosso = buffer.count("ROSSO")
    verde = buffer.count("VERDE")
    spento = buffer.count("SPENTO")
    total = len(buffer)

    # --- Filtro ROSSO ---
    # Richiede che il rosso persista per una frazione significativa del buffer.
    # Con buffer=100 e loop=0.1s, 15 campioni = 1.5 secondi.
    # Se l'utente riduce il buffer (es. a 35), 15 campioni = 1.5s su 3.5s totali.
    if rosso >= 15: return "ROSSO"

    if spento / total >= STEADY_STATE_THRESHOLD: return "SPENTO"
    if verde / total >= STEADY_STATE_THRESHOLD: return "VERDE"
    if verde > 0 and spento > 0: return "ATTESA"

    return "VERDE" if verde > spento else "SPENTO"


def write_debug_log(ts_str, rgb, inst, comp):
    global current_log_file_path, current_log_line_count
    if not DEBUG_LOGGING_ENABLED: return
    try:
        if not current_log_file_path or current_log_line_count >= MAX_DEBUG_LINES:
            fn = f"debug_log_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.csv"
            current_log_file_path = os.path.join(LOG_DIR, fn)
            with open(current_log_file_path, 'w') as f: f.write(CSV_HEADER)
            current_log_line_count = 1
        line = f"{ts_str},{rgb['R']},{rgb['G']},{rgb['B']},{inst or 'ERR'},{comp}\n"
        with open(current_log_file_path, 'a') as f:
            f.write(line)
        current_log_line_count += 1
    except:
        pass


# --- MQTT Helpers ---
def on_connect(c, u, f, rc, p):
    global is_mqtt_connected
    is_mqtt_connected = (rc == 0)
    print(f"{'✅' if rc == 0 else '❌'} MQTT Connect: RC={rc}")


def on_disconnect(c, u, f, rc, p):
    global is_mqtt_connected
    is_mqtt_connected = False
    print(f"⚠️ MQTT Disconnect: RC={rc}")


def ensure_mqtt_connection(client):
    if not client.is_connected():
        print("⚠️ Check MQTT... Disconnesso. Riconnessione...")
        try:
            client.reconnect()
            for _ in range(20):  # Max 2s
                if client.is_connected():
                    print("♻️ Riconnesso.");
                    return True
                client.loop(0.1);
                time.sleep(0.1)
            print("❌ Fail Riconnessione.")
            return False
        except Exception as e:
            print(f"❌ Err MQTT: {e}");
            return False
    return True


# --- Main ---
def main():
    global is_mqtt_connected, DEBUG_LOGGING_ENABLED
    data = carica_calibrazione()
    if not data: return
    if DEBUG_LOGGING_ENABLED: os.makedirs(LOG_DIR, exist_ok=True)

    # --- MODIFICA V 1.29: Rispetto totale config ---
    # Nessuna forzatura. Se l'utente ha messo 35, usiamo 35.
    BUFFER_SIZE = data.get('buffer_size', 100)  # Default 100 se manca la chiave
    print(f"ℹ️  Buffer operativo da config: {BUFFER_SIZE} letture.")
    # --- FINE MODIFICA ---

    sensor = inizializza_sensore(data.get('integration_time', 150), data.get('gain', 4))
    if not sensor: return

    mid = data.get("machine_id", "Unknown")
    topic_status = f"bma/{mid}/semaforo/stato"

    buffer = deque(maxlen=BUFFER_SIZE)
    print("Inizializzazione buffer (attendere)...")
    for i in range(20):
        st, _ = get_instant_status(sensor, data)
        buffer.append(st if st else "SPENTO")
        time.sleep(LOOP_SLEEP_TIME)

    # Fill remaining if any
    while len(buffer) < BUFFER_SIZE: buffer.append("SPENTO")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=mid)
    client.on_connect, client.on_disconnect = on_connect, on_disconnect
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.reconnect_delay_set(1, 30)

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        print("⏳ Waiting MQTT...")
        for _ in range(50):
            if is_mqtt_connected: break
            client.loop(0.1)
        print("🚀 Monitoraggio AVVIATO.")
    except Exception as e:
        print(f"❌ Err MQTT init: {e}")

    pub_state = None
    prev_comp = None
    last_chg = 0

    try:
        while True:
            cur_st, cur_rgb = get_instant_status(sensor, data)
            if cur_st:
                buffer.append(cur_st)
                comp_st = analyze_state_buffer(buffer)

                # Logica Pubblicazione
                to_pub = None
                if comp_st != "SPENTO":
                    to_pub = comp_st
                    if comp_st != pub_state: last_chg = time.time()
                else:
                    if time.time() - last_chg > STATE_PERSISTENCE_SECONDS:
                        to_pub = "SPENTO"
                    else:
                        to_pub = pub_state

                # Debug File
                if comp_st != prev_comp:
                    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                    write_debug_log(ts, cur_rgb, cur_st, comp_st)
                    prev_comp = comp_st

                # MQTT Send
                if to_pub != pub_state:
                    now = time.time()
                    dt_s = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))
                    payload = json.dumps({"message": {
                        "stato": to_pub, "machine_id": mid,
                        "timestamp": now, "datetime_str": dt_s
                    }})

                    if ensure_mqtt_connection(client):
                        try:
                            inf = client.publish(topic_status, payload, qos=1, retain=True)
                            client.publish(MQTT_TRIGGER_TOPIC, payload, qos=1, retain=True)
                            inf.wait_for_publish(2)
                            print(f"[{dt_s}] Nuovo Stato: {to_pub} -> Inviato.")
                            pub_state = to_pub
                            last_chg = now
                        except Exception as e:
                            print(f"⚠️ Err Pub: {e}")
                            pub_state = None
                    else:
                        print(f"[{dt_s}] Nuovo Stato: {to_pub} -> FAIL (No Conn).")
                        pub_state = None

            client.loop(LOOP_SLEEP_TIME)

    except KeyboardInterrupt:
        print("\n🛑 Stop.")
    finally:
        client.disconnect()
        print("✅ Terminato.")


if __name__ == "__main__":
    main()