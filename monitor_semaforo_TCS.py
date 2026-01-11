#!/usr/bin/env python3
# ---
# File: monitor_semaforo_TCS.py
# Directory: [root]
# Ultima Modifica: 2026-01-11
# Versione: 1.26 (FULL)
# ---

"""
MONITOR SEMAFORO - Versione TCS34725 (4 Stati)

V 1.26:
- SOGLIA LUMINOSITÀ AUMENTATA: 'MIN_LUMINOSITY_THRESHOLD' portata a 100.
  Questo elimina i falsi positivi "ROSSO" al buio (fantasmi).
- INCLUDE FIX MQTT: Riconnessione attiva prima della pubblicazione.
- INCLUDE FIX RACE CONDITION: Attesa connessione all'avvio.
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
LOOP_SLEEP_TIME = 0.05  # 50ms per ciclo (molto reattivo)
STATE_PERSISTENCE_SECONDS = 0.5

# --- MODIFICA V 1.26: SOGLIA DI SICUREZZA ---
# Se (R + G + B) < 100, forziamo lo stato a SPENTO.
# Questo elimina i disturbi del sensore al buio che vengono scambiati per colori.
MIN_LUMINOSITY_THRESHOLD = 100
# --- FINE MODIFICA V 1.26 ---

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
STEADY_STATE_THRESHOLD = 0.90  # Default, sovrascritto dalla config


# --- Inizializzazione Hardware ---

def inizializza_sensore(integration_time, gain):
    """Inizializza il sensore TCS34725."""
    print("🔧 Inizializzazione sensore TCS34725...")
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        sensor = adafruit_tcs34725.TCS34725(i2c)
        sensor.integration_time = integration_time

        if gain in [1, 4, 16, 60]:
            sensor.gain = gain
        else:
            print(f"   ⚠️ Gain {gain} non valido, imposto 4x.")
            sensor.gain = 4
            gain = 4

        print(f"✅ Sensore inizializzato (Time: {integration_time}ms, Gain: {gain}x).")
        return sensor
    except Exception as e:
        print(f"❌ ERRORE: Impossibile trovare il sensore TCS34725.")
        print(f"   Dettagli: {e}")
        return None


def carica_calibrazione():
    """Carica i dati di calibrazione dal file JSON."""
    global DEBUG_LOGGING_ENABLED, STEADY_STATE_THRESHOLD

    if not os.path.exists(CALIBRATION_FILE):
        print(f"❌ ERRORE: File di calibrazione non trovato!")
        print(f"   Esegui prima 'utils/calibra_sensore.py'")
        return None

    try:
        with open(CALIBRATION_FILE, 'r') as f:
            data = json.load(f)
        if "verde" not in data or "non_verde" not in data or "buio" not in data:
            print("❌ ERRORE: File di calibrazione incompleto.")
            return None

        DEBUG_LOGGING_ENABLED = data.get('debug_logging', False)
        if DEBUG_LOGGING_ENABLED:
            print("ℹ️  Logging di Debug Avanzato ATTIVO (scrive su /LOG)")

        soglia_percent = data.get('steady_state_threshold', 90)
        STEADY_STATE_THRESHOLD = soglia_percent / 100.0
        print(f"ℹ️  Soglia di stabilità impostata a: {soglia_percent}%")

        print(f"✅ Dati di calibrazione caricati da '{CALIBRATION_FILE}'")
        return data
    except Exception as e:
        print(f"❌ ERRORE durante la lettura del file JSON: {e}")
        return None


# --- Funzioni di Lettura e Analisi ---

def leggi_rgb_attuale(sens):
    """Esegue una singola lettura RGB, con fallback."""
    try:
        result = sens.color_rgb_bytes
        if len(result) >= 3: return result[:3]
    except Exception:
        pass
    try:
        raw = sens.color_raw
        return min(255, int(raw[0] / 256)), min(255, int(raw[1] / 256)), min(255, int(raw[2] / 256))
    except Exception:
        return 0, 0, 0


def leggi_rgb_stabilizzato(sensor, campioni=CAMPIONI_PER_LETTURA):
    """Legge il sensore 'campioni' volte e restituisce i valori medi R, G, B."""
    tot_r, tot_g, tot_b, letture_valide = 0, 0, 0, 0
    for _ in range(campioni):
        try:
            r, g, b = leggi_rgb_attuale(sensor)
            # Filtro Errori I2C (0,0,0) - Ignoriamo letture nulle
            if r == 0 and g == 0 and b == 0:
                continue
            letture_valide += 1;
            tot_r += r;
            tot_g += g;
            tot_b += b
        except Exception:
            pass
        time.sleep(0.01)

    if letture_valide == 0: return {"R": 0, "G": 0, "B": 0}
    return {"R": int(tot_r / letture_valide), "G": int(tot_g / letture_valide), "B": int(tot_b / letture_valide)}


def calcola_distanza_rgb(rgb1, rgb2):
    r1, g1, b1 = rgb1['R'], rgb1['G'], rgb1['B']
    r2, g2, b2 = rgb2['R'], rgb2['G'], rgb2['B']
    return ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5


def get_instant_status(sensor, calib_data):
    """Determina lo stato istantaneo basandosi su distanza colori e luminosità."""
    rgb_medio = leggi_rgb_stabilizzato(sensor)

    # Gestione Errore Hardware (lettura 0,0,0 persistente)
    if rgb_medio["R"] == 0 and rgb_medio["G"] == 0 and rgb_medio["B"] == 0:
        return None, rgb_medio

    # --- MODIFICA V 1.26: CHECK LUMINOSITÀ MINIMA ---
    # Se la luce totale è troppo bassa, è SPENTO a prescindere dal colore.
    somma_lux = rgb_medio["R"] + rgb_medio["G"] + rgb_medio["B"]
    if somma_lux < MIN_LUMINOSITY_THRESHOLD:
        return "SPENTO", rgb_medio
    # --- FINE MODIFICA ---

    dist_verde = calcola_distanza_rgb(rgb_medio, calib_data['verde'])
    dist_rosso = calcola_distanza_rgb(rgb_medio, calib_data['non_verde'])
    dist_buio = calcola_distanza_rgb(rgb_medio, calib_data['buio'])

    distanze = {"VERDE": dist_verde, "ROSSO": dist_rosso, "SPENTO": dist_buio}
    stato_piu_vicino = min(distanze, key=distanze.get)
    return stato_piu_vicino, rgb_medio


def analyze_state_buffer(buffer):
    """Analizza il buffer (sliding window) per determinare lo stato stabile."""
    rosso_count = buffer.count("ROSSO")
    verde_count = buffer.count("VERDE")
    spento_count = buffer.count("SPENTO")
    total = len(buffer)

    # REGOLA 1: Priorità ROSSO (Richiede almeno 3 campioni per evitare glitch singoli)
    if rosso_count >= 3: return "ROSSO"

    # REGOLA 2/3: Stati Fissi (SPENTO o VERDE) basati sulla soglia %
    if spento_count / total >= STEADY_STATE_THRESHOLD: return "SPENTO"
    if verde_count / total >= STEADY_STATE_THRESHOLD: return "VERDE"

    # REGOLA 4: Lampeggio (ATTESA)
    if verde_count > 0 and spento_count > 0: return "ATTESA"

    # Fallback
    if verde_count > spento_count:
        return "VERDE"
    else:
        return "SPENTO"


def write_debug_log(timestamp_str, rgb, instant_state, composite_state):
    global current_log_file_path, current_log_line_count, DEBUG_LOGGING_ENABLED
    if not DEBUG_LOGGING_ENABLED: return

    try:
        if current_log_file_path is None or current_log_line_count >= MAX_DEBUG_LINES:
            now_filename = datetime.now().strftime('%Y-%m-%d_%H%M%S')
            new_filename = f"debug_log_{now_filename}.csv"
            current_log_file_path = os.path.join(LOG_DIR, new_filename)
            with open(current_log_file_path, 'w') as f: f.write(CSV_HEADER)
            current_log_line_count = 1

        st_ist = instant_state if instant_state else "ERR"
        line = f"{timestamp_str},{rgb['R']},{rgb['G']},{rgb['B']},{st_ist},{composite_state}\n"
        with open(current_log_file_path, 'a') as f:
            f.write(line)
        current_log_line_count += 1
    except Exception as e:
        print(f"   ⚠️ Errore scrittura debug log: {e}")


# --- MQTT Functions ---
def on_connect(client, userdata, flags, rc, properties):
    global is_mqtt_connected
    if rc == 0:
        print(f"✅ Connesso al broker MQTT! (RC: {rc})")
        is_mqtt_connected = True
    else:
        print(f"❌ Connessione MQTT fallita, codice: {rc}.")
        is_mqtt_connected = False


def on_disconnect(client, userdata, flags, reason_code, properties):
    global is_mqtt_connected
    is_mqtt_connected = False
    print(f"⚠️ Disconnesso dal broker MQTT. RC: {reason_code}")


def ensure_mqtt_connection(client):
    """Verifica e forza riconnessione se necessario prima di pubblicare."""
    global is_mqtt_connected
    if not client.is_connected():
        print("⚠️ MQTT Disconnesso. Tentativo di riconnessione immediata...")
        try:
            client.reconnect()
            timeout = 0
            # Attesa attiva della riconnessione (max 2 secondi)
            while not client.is_connected() and timeout < 20:
                client.loop(timeout=0.1)
                time.sleep(0.1)
                timeout += 1
            if client.is_connected():
                print("♻️  Riconnessione riuscita!")
                return True
            else:
                print("❌ Riconnessione fallita.")
                return False
        except Exception as e:
            print(f"❌ Errore riconnessione: {e}")
            return False
    return True


# --- Main ---
def main():
    global is_mqtt_connected, DEBUG_LOGGING_ENABLED
    calibrated_data = carica_calibrazione()
    if not calibrated_data: return

    if DEBUG_LOGGING_ENABLED: os.makedirs(LOG_DIR, exist_ok=True)

    integration_time = calibrated_data.get('integration_time', 150)
    gain = calibrated_data.get('gain', 4)
    BUFFER_SIZE = calibrated_data.get('buffer_size', 35)

    # Check parametri minimi
    if integration_time == 150 and 'integration_time' not in calibrated_data:
        print("⚠️  'integration_time' default: 150ms")
    if gain == 4 and 'gain' not in calibrated_data:
        print("⚠️  'gain' default: 4x")

    print(f"ℹ️  Buffer operativo: {BUFFER_SIZE} letture.")

    sensor = inizializza_sensore(integration_time, gain)
    if not sensor: return

    MACHINE_ID = calibrated_data.get("machine_id", "Unknown")
    MQTT_TOPIC_STATUS = f"bma/{MACHINE_ID}/semaforo/stato"

    # Pre-riempimento buffer
    visual_state_buffer = deque(maxlen=BUFFER_SIZE)
    print("Avvio buffer...")
    for i in range(20):
        st, _ = get_instant_status(sensor, calibrated_data)
        if st:
            visual_state_buffer.append(st)
            print(f"   Lettura {i + 1} -> {st}   ", end="\r")
        else:
            print(f"   Lettura {i + 1} -> ERR ", end="\r")
        time.sleep(LOOP_SLEEP_TIME)
    if not visual_state_buffer:
        for _ in range(BUFFER_SIZE): visual_state_buffer.append("SPENTO")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MACHINE_ID)
    client.on_connect, client.on_disconnect = on_connect, on_disconnect
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        print("\n⏳ Attesa stabilità MQTT (Max 5s)...")
        t = 0
        while not is_mqtt_connected and t < 50:
            client.loop(0.1);
            t += 1
        if is_mqtt_connected:
            print("🚀 Monitoraggio avviato.")
        else:
            print("⚠️  MQTT non pronto, avvio in background.")
    except Exception as e:
        print(f"❌ Errore MQTT iniziale: {e}")

    stato_pubblicato = None
    prev_composite_state = None
    last_published_change_time = 0

    try:
        while True:
            stato_corrente, rgb_corrente = get_instant_status(sensor, calibrated_data)

            # Se la lettura è valida (non None)
            if stato_corrente:
                visual_state_buffer.append(stato_corrente)
                stato_composito = analyze_state_buffer(visual_state_buffer)

                stato_da_pubblicare = None
                if stato_composito != "SPENTO":
                    stato_da_pubblicare = stato_composito
                    if stato_composito != stato_pubblicato:
                        last_published_change_time = time.time()
                else:
                    # Ritardo per lo SPENTO per evitare flickering
                    if time.time() - last_published_change_time > STATE_PERSISTENCE_SECONDS:
                        stato_da_pubblicare = "SPENTO"
                    else:
                        stato_da_pubblicare = stato_pubblicato

                        # Debug Log
                if stato_composito != prev_composite_state:
                    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                    write_debug_log(now_str, rgb_corrente, stato_corrente, stato_composito)
                    prev_composite_state = stato_composito

                    # Pubblicazione MQTT
                if stato_da_pubblicare != stato_pubblicato:
                    ts = time.time()
                    dt_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))

                    payload = json.dumps({"message": {
                        "stato": stato_da_pubblicare, "machine_id": MACHINE_ID,
                        "timestamp": ts, "datetime_str": dt_str
                    }})

                    # Connessione Sicura prima di Inviare
                    if ensure_mqtt_connection(client):
                        try:
                            info = client.publish(MQTT_TOPIC_STATUS, payload, qos=1, retain=True)
                            client.publish(MQTT_TRIGGER_TOPIC, payload, qos=1, retain=True)
                            info.wait_for_publish(2)
                            print(f"[{dt_str}] Stato: {stato_da_pubblicare} -> MQTT OK")
                            stato_pubblicato = stato_da_pubblicare
                            last_published_change_time = ts
                        except Exception as e:
                            print(f"⚠️ Errore Pubblicazione: {e}")
                            # Forziamo il ri-invio al prossimo giro
                            stato_pubblicato = None
                    else:
                        print(f"[{dt_str}] Stato: {stato_da_pubblicare} -> MQTT FAIL")
                        stato_pubblicato = None

            client.loop(timeout=LOOP_SLEEP_TIME)

    except KeyboardInterrupt:
        print("\n🛑 Stop.")
    finally:
        print("🧹 Rilascio risorse...")
        client.disconnect()
        print("✅ Programma terminato.")


if __name__ == "__main__":
    main()