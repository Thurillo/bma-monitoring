#!/usr/bin/env python3
# ---
# File: monitor_semaforo_TCS.py
# Directory: [root]
# Ultima Modifica: 2025-11-14
# Versione: 1.18
# ---

"""
MONITOR SEMAFORO - Versione TCS34725 (4 Stati)

V 1.18:
- INVERSIONE LOGICA (come da richiesta utente).
- Riscritto 'analyze_state_buffer'.
- Aggiunta STEADY_STATE_THRESHOLD = 0.90 (90%).
- VERDE e SPENTO ora richiedono il 90% del buffer
  per essere considerati 'fissi'.
- ATTESA è definito come un mix di VERDE e SPENTO
  che non raggiunge le soglie 'fisse'.
- Rimosse costanti 'BLINK_THRESHOLD_PERCENT' e
  'MIN_TRANSITIONS_FOR_BLINK' (non più necessarie).
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
LOOP_SLEEP_TIME = 0.1
STATE_PERSISTENCE_SECONDS = 0.5
# --- MODIFICA V 1.18: Nuova logica di soglia ---
STEADY_STATE_THRESHOLD = 0.90  # (90%)
# --- FINE MODIFICA V 1.18 ---

# --- CONFIGURAZIONE DEBUG LOGGING (V 1.06) ---
MAX_DEBUG_LINES = 5000
# --- FINE CONFIGURAZIONE ---

# --- CONFIGURAZIONE MQTT (e Percorsi) ---
MQTT_BROKER = "192.168.20.163"
MQTT_PORT = 1883
# --- MODIFICA V 1.15: Ripristino Costanti ---
MQTT_USERNAME = "shima"
MQTT_PASSWORD = "shima"
# --- FINE MODIFICA V 1.15 ---
MQTT_TRIGGER_TOPIC = "bma/cambiostato"
# ----------------------------------------------
# --- MODIFICA V 1.14: Ripristino Costanti ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "config")
CALIBRATION_FILE = os.path.join(CONFIG_DIR, "calibrazione.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "LOG")
# --- FINE MODIFICA V 1.14 ---
# ----------------------------------------

is_mqtt_connected = False
current_log_file_path = None
current_log_line_count = 0
CSV_HEADER = "Timestamp,R,G,B,StatoIstantaneo,StatoComposito\n"
DEBUG_LOGGING_ENABLED = False


# ----------------------------------------

# --- Inizializzazione Hardware ---

# --- MODIFICA V 1.10: Correzione GAIN ---
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
            gain = 4  # Aggiorna la variabile per il log

        print(f"✅ Sensore inizializzato (Time: {integration_time}ms, Gain: {gain}x).")
        return sensor
    except Exception as e:
        print(f"❌ ERRORE: Impossibile trovare il sensore TCS34725.")
        print(f"   Dettagli: {e}")
        return None


# --- FINE MODIFICA V 1.10 ---

def carica_calibrazione():
    """Carica i dati di calibrazione dal file JSON."""
    global DEBUG_LOGGING_ENABLED

    if not os.path.exists(CALIBRATION_FILE):
        print(f"❌ ERRORE: File di calibrazione non trovato!")
        print(f"   Esegui prima 'utils/calibra_sensore.py'")
        print(f"   Percorso cercato: {CALIBRATION_FILE}")
        return None

    try:
        with open(CALIBRATION_FILE, 'r') as f:
            data = json.load(f)
        if "verde" not in data or "non_verde" not in data or "buio" not in data:
            print("❌ ERRORE: File di calibrazione incompleto (mancano i colori).")
            print("   Esegui 'utils/calibra_sensore.py' per ricalibrare.")
            return None

        DEBUG_LOGGING_ENABLED = data.get('debug_logging', False)
        if DEBUG_LOGGING_ENABLED:
            print("ℹ️  Logging di Debug Avanzato ATTIVO (scrive su /LOG)")
        else:
            print("ℹ️  Logging di Debug Avanzato DISATTIVATO.")

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
    """Calcola la distanza Euclidea tra due colori RGB."""
    r1, g1, b1 = rgb1['R'], rgb1['G'], rgb1['B']
    r2, g2, b2 = rgb2['R'], rgb2['G'], rgb2['B']
    return ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5


def get_instant_status(sensor, calib_data):
    """Determina lo stato istantaneo (ROSSO, VERDE, SPENTO)."""
    rgb_medio = leggi_rgb_stabilizzato(sensor)

    if not rgb_medio: return "SPENTO", {"R": 0, "G": 0, "B": 0}

    dist_verde = calcola_distanza_rgb(rgb_medio, calib_data['verde'])
    dist_rosso = calcola_distanza_rgb(rgb_medio, calib_data['non_verde'])
    dist_buio = calcola_distanza_rgb(rgb_medio, calib_data['buio'])

    distanze = {
        "VERDE": dist_verde,
        "ROSSO": dist_rosso,
        "SPENTO": dist_buio
    }

    stato_piu_vicino = min(distanze, key=distanze.get)
    return stato_piu_vicino, rgb_medio


# --- MODIFICA V 1.18: Funzione di analisi riscritta ---
def analyze_state_buffer(buffer):
    """
    Analizza il buffer per determinare lo stato composito.
    (Logica V 1.18 - Inversione delle priorità)
    """
    rosso_count = buffer.count("ROSSO")
    verde_count = buffer.count("VERDE")
    spento_count = buffer.count("SPENTO")

    # REGOLA 1: ROSSO ha la priorità assoluta.
    # Se c'è ANCHE UN SOLO "ROSSO" nel buffer, lo stato è ROSSO.
    if rosso_count > 0:
        return "ROSSO"

    # REGOLA 2: SPENTO (Fisso)
    # Se non c'è ROSSO, controlliamo se è SPENTO.
    # Richiede che il 90% (STEADY_STATE_THRESHOLD) del buffer sia SPENTO.
    if spento_count / len(buffer) >= STEADY_STATE_THRESHOLD:
        return "SPENTO"

    # REGOLA 3: VERDE (Fisso)
    # Se non è ROSSO e non è SPENTO Fisso, controlliamo se è VERDE Fisso.
    # Richiede che il 90% (STEADY_STATE_THRESHOLD) del buffer sia VERDE.
    if verde_count / len(buffer) >= STEADY_STATE_THRESHOLD:
        return "VERDE"

    # REGOLA 4: ATTESA (Lampeggiante)
    # Se non è ROSSO, né SPENTO Fisso, né VERDE Fisso,
    # ma contiene SIA VERDE CHE SPENTO, allora deve essere ATTESA.
    if verde_count > 0 and spento_count > 0:
        return "ATTESA"

    # REGOLA 5: Fallback (Stato di transizione o buffer non ancora pieno)
    # Se non è nessuna delle precedenti (es. buffer solo VERDE ma < 90%),
    # manteniamo lo stato dominante tra VERDE e SPENTO.
    if verde_count > spento_count:
        return "VERDE"
    else:
        return "SPENTO"


# --- FINE MODIFICA V 1.18 ---

# --- FUNZIONE LOGGING V 1.06 (STRATEGIA 1): ROTAZIONE FILE ---
def write_debug_log(timestamp_str, rgb, instant_state, composite_state):
    """Scrive sul file di debug CSV, gestendo la rotazione del file."""
    global current_log_file_path, current_log_line_count, DEBUG_LOGGING_ENABLED

    if not DEBUG_LOGGING_ENABLED:
        return

    try:
        if current_log_file_path is None or current_log_line_count >= MAX_DEBUG_LINES:
            now_filename = datetime.now().strftime('%Y-%m-%d_%H%M%S')
            new_filename = f"debug_log_{now_filename}.csv"
            current_log_file_path = os.path.join(LOG_DIR, new_filename)

            with open(current_log_file_path, 'w') as f:
                f.write(CSV_HEADER)
            current_log_line_count = 1

        line = f"{timestamp_str},{rgb['R']},{rgb['G']},{rgb['B']},{instant_state},{composite_state}\n"

        with open(current_log_file_path, 'a') as f:
            f.write(line)
        current_log_line_count += 1

    except Exception as e:
        print(f"   ⚠️ Errore scrittura debug log: {e}")


# --- FINE FUNZIONE V 1.06 ---


# --- Funzioni MQTT ---
def on_connect(client, userdata, flags, rc, properties):
    """Callback per quando ci si connette al broker."""
    global is_mqtt_connected
    if rc == 0:
        print(f"✅ Connesso al broker MQTT! (Flags: {flags}, RC: {rc})")
        is_mqtt_connected = True
    else:
        print(f"❌ Connessione MQTT fallita, codice: {rc}.")
        is_mqtt_connected = False


def on_disconnect(client, userdata, flags, reason_code, properties):
    """Callback per quando ci si disconnette."""
    global is_mqtt_connected
    is_mqtt_connected = False
    print(f"⚠️ Disconnesso dal broker MQTT. Reason code: {reason_code}")
    if reason_code != 0:
        print("   Tentativo di riconnessione automatica gestito da Paho-MQTT...")


# --- Ciclo Principale ---

def main():
    global is_mqtt_connected, DEBUG_LOGGING_ENABLED

    calibrated_data = carica_calibrazione()
    if not calibrated_data:
        print("Impossibile avviare. File di calibrazione mancante o corrotto.")
        return

    if DEBUG_LOGGING_ENABLED:
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
        except Exception as e:
            print(f"❌ ERRORE: Impossibile creare la directory LOG: {e}")
            print("   Il debug logging CSV sarà disabilitato.")
            DEBUG_LOGGING_ENABLED = False

    integration_time = calibrated_data.get('integration_time', 250)
    gain = calibrated_data.get('gain', 4)

    # --- MODIFICA V 1.16: Carica BUFFER_SIZE da config ---
    BUFFER_SIZE = calibrated_data.get('buffer_size', 35)
    # --- FINE MODIFICA V 1.16 ---

    if integration_time == 250 and 'integration_time' not in calibrated_data:
        print("⚠️  'integration_time' non trovato in config, uso default: 250ms")
    if gain == 4 and 'gain' not in calibrated_data:
        print("⚠️  'gain' non trovato in config, uso default: 4x")
    # --- MODIFICA V 1.16 ---
    if BUFFER_SIZE == 35 and 'buffer_size' not in calibrated_data:
        print("⚠️  'buffer_size' non trovato in config, uso default: 35")
    print(f"ℹ️  Buffer operativo impostato a {BUFFER_SIZE} letture.")
    # --- FINE MODIFICA V 1.16 ---

    sensor = inizializza_sensore(integration_time, gain)
    if not sensor:
        print("Impossibile avviare. Controlla hardware.")
        return

    if "machine_id" not in calibrated_data or not calibrated_data["machine_id"]:
        print(f"❌ ERRORE: 'machine_id' non trovato o non impostato in '{CALIBRATION_FILE}'.")
        print(f"   Esegui 'utils/calibra_sensore.py' e imposta un ID Macchina (Opzione 4).")
        return

    MACHINE_ID = calibrated_data.get("machine_id")
    MQTT_TOPIC_STATUS = f"bma/{MACHINE_ID}/semaforo/stato"

    # --- MODIFICA V 1.13 (invariata) ---
    INIT_BUFFER_SIZE = 20
    print(f"Avvio... (Inizializzazione buffer... {INIT_BUFFER_SIZE} letture)")
    # --- MODIFICA V 1.16: Usa il BUFFER_SIZE caricato ---
    visual_state_buffer = deque(maxlen=BUFFER_SIZE)
    # --- FINE MODIFICA V 1.16 ---

    for i in range(INIT_BUFFER_SIZE):
        stato_iniziale, _ = get_instant_status(sensor, calibrated_data)
        visual_state_buffer.append(stato_iniziale)
        print(f"   Lettura... {i + 1}/{INIT_BUFFER_SIZE} -> {stato_iniziale}   ", end="\r")
        time.sleep(LOOP_SLEEP_TIME)

    print("\n✅ Inizializzazione completata.")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MACHINE_ID)
    client.on_connect, client.on_disconnect = on_connect, on_disconnect
    # --- MODIFICA V 1.15: Ripristino Costanti ---
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    # --- FINE MODIFICA V 1.15 ---
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
    except Exception as e:
        print(f"❌ Errore di connessione MQTT iniziale: {e}")

    stato_pubblicato = None
    last_published_change_time = 0

    prev_composite_state = None

    print("Monitoraggio attivo.")

    try:
        while True:
            stato_corrente, rgb_corrente = get_instant_status(sensor, calibrated_data)
            visual_state_buffer.append(stato_corrente)

            stato_composito = analyze_state_buffer(visual_state_buffer)

            stato_da_pubblicare = None
            if stato_composito != "SPENTO":
                stato_da_pubblicare = stato_composito
                if stato_composito != stato_pubblicato:
                    last_published_change_time = time.time()
            else:
                if time.time() - last_published_change_time > STATE_PERSISTENCE_SECONDS:
                    stato_da_pubblicare = "SPENTO"
                else:
                    stato_da_pubblicare = stato_pubblicato

            if stato_composito != prev_composite_state:
                now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                write_debug_log(now_str, rgb_corrente, stato_corrente, stato_composito)
                prev_composite_state = stato_composito

            if stato_da_pubblicare != stato_pubblicato:
                stato_pubblicato = stato_da_pubblicare
                last_published_change_time = time.time()

                timestamp = time.time()
                datetime_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))

                payload_data = {
                    "stato": stato_pubblicato, "machine_id": MACHINE_ID,
                    "timestamp": timestamp, "datetime_str": datetime_str
                }
                payload = json.dumps({"message": payload_data})

                if is_mqtt_connected:
                    try:
                        client.publish(MQTT_TOPIC_STATUS, payload, qos=1, retain=True)
                        client.publish(MQTT_TRIGGER_TOPIC, payload, qos=1, retain=True)
                        print(
                            f"[{datetime_str}] Stato Pubblicato: {stato_pubblicato}. Invio trigger a '{MQTT_TRIGGER_TOPIC}'...")
                    except Exception as e:
                        print(f"   ⚠️ Errore during la pubblicazione MQTT: {e}. In attesa di riconnessione...")
                else:
                    print(f"[{datetime_str}] Rilevato cambio: {stato_pubblicato}. MQTT OFFLINE. Messaggio non inviato.")

            client.loop(timeout=LOOP_SLEEP_TIME)

    except KeyboardInterrupt:
        print("\n🛑 Chiusura del programma...")
    finally:
        print("🧹 Rilascio risorse...")
        client.disconnect()
        print("✅ Programma terminato.")


if __name__ == "__main__":
    main()