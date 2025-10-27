#!/usr/bin/env python3
"""
MONITOR SEMAFORO - Versione TCS34725 (4 Stati)

Questo script sostituisce la logica OpenCV con un sensore TCS34725.
Implementa una logica a 4 stati (ROSSO, VERDE, ATTESA, SPENTO) analizzando
un buffer di letture per distinguere tra fisso e lampeggiante.

Versione stabile con output di log su singola riga (con timestamp).
"""

import time
import json
import sys
import os
from collections import deque
from datetime import datetime  # Importato per il timestamp

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
# --- MODIFICHE PER REATTIVITA' < 6s E CATTURA LAMPEGGIO ---
# Numero di campioni da mediare per una singola lettura (più alto = più stabile)
CAMPIONI_PER_LETTURA = 1  # Era 2 (Ridotto per velocità)
# Numero di letture da tenere in memoria (più alto = analisi più lunga)
BUFFER_SIZE = 20  # Era 10 (Aumentato per stabilità)
# Pausa tra i cicli di lettura
LOOP_SLEEP_TIME = 0.01  # Era 0.05 (Ridotto per velocità)
# -----------------------------------------
# Secondi di "SPENTO" prima di pubblicare lo stato SPENTO
STATE_PERSISTENCE_SECONDS = 0.5  # Era 3.0
# Soglia per il lampeggio: % di letture "SPENTO" nel buffer per definirlo "ATTESA"
BLINK_THRESHOLD_PERCENT = 0.15  # (15%)

# --- NUOVA LOGICA ANTI-TRANSIZIONE ---
# Per essere "ATTESA", il buffer deve avere almeno questo numero di cambi (V->S o S->V)
# Questo previene che una transizione V->S (che ha 1 solo cambio) venga letta come ATTESA.
MIN_TRANSITIONS_FOR_BLINK = 3

# --- CONFIGURAZIONE MQTT (e Percorsi) ---
MQTT_BROKER = "192.168.20.163"
MQTT_PORT = 1883
MQTT_USERNAME = "shima"
MQTT_PASSWORD = "shima"
# --- MODIFICA RICHIESTA ---
MACHINE_ID = "macchina_01_TCS"
MQTT_TOPIC_STATUS = f"bma/{MACHINE_ID}/semaforo/stato"  # Questo si aggiornerà automaticamente
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "config")
CALIBRATION_FILE = os.path.join(CONFIG_DIR, "calibrazione.json")


# --- Inizializzazione Hardware ---

def inizializza_sensore():
    """Inizializza il sensore TCS34725."""
    print("🔧 Inizializzazione sensore TCS34725...")
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        sensor = adafruit_tcs34725.TCS34725(i2c)
        # --- MODIFICA PER AUMENTARE SENSIBILITA' ---
        sensor.integration_time = 250  # Aumentato da 150
        sensor.gain = 16  # Aumentato da 4
        # -------------------------------------------
        print("✅ Sensore inizializzato (con gain/time aumentati).")
        return sensor
    except Exception as e:
        print(f"❌ ERRORE: Impossibile trovare il sensore TCS34725.")
        print(f"   Dettagli: {e}")
        return None


def carica_calibrazione():
    """Carica i dati di calibrazione dal file JSON."""
    if not os.path.exists(CALIBRATION_FILE):
        print(f"❌ ERRORE: File di calibrazione non trovato!")
        print(f"   Esegui prima 'utils/calibra_sensore.py'")
        print(f"   Percorso cercato: {CALIBRATION_FILE}")
        return None

    try:
        with open(CALIBRATION_FILE, 'r') as f:
            data = json.load(f)
        if "verde" not in data or "non_verde" not in data or "buio" not in data:
            print("❌ ERRORE: File di calibrazione incompleto.")
            return None

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
        time.sleep(0.01)  # Piccola pausa tra i campioni

    if letture_valide == 0: return {"R": 0, "G": 0, "B": 0}
    return {"R": int(tot_r / letture_valide), "G": int(tot_g / letture_valide), "B": int(tot_b / letture_valide)}


def calcola_distanza_rgb(rgb1, rgb2):
    """Calcola la distanza Euclidea tra due colori RGB."""
    r1, g1, b1 = rgb1['R'], rgb1['G'], rgb1['B']
    r2, g2, b2 = rgb2['R'], rgb2['G'], rgb2['B']
    return ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5


def get_instant_status(sensor, calib_data):
    """
    Legge il sensore e determina lo stato istantaneo (ROSSO, VERDE, SPENTO)
    in base alla distanza minima dai valori calibrati.

    Restituisce (stato, rgb_letto)
    """
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


def analyze_state_buffer(buffer):
    """
    Analizza il buffer delle letture per determinare lo stato composito
    (ROSSO, VERDE, ATTESA, SPENTO).
    """
    rosso_count = buffer.count("ROSSO")
    verde_count = buffer.count("VERDE")
    spento_count = buffer.count("SPENTO")

    # REGOLA 1: Se c'è ROSSO nel buffer, è sempre ROSSO (massima priorità)
    if rosso_count > 0:
        return "ROSSO"

    # REGOLA 2: Se c'è VERDE e non c'è ROSSO
    if verde_count > 0:
        percent_spento = spento_count / len(buffer)

        # È un candidato al lampeggio (contiene sia VERDE che SPENTO)?
        if percent_spento >= BLINK_THRESHOLD_PERCENT:

            # --- NUOVA LOGICA ---
            # Controlla se è un VERO lampeggio (tanti cambi V->S)
            # o solo una transizione (1-2 cambi V->S)
            transitions = 0
            for i in range(len(buffer) - 1):
                # Controlla solo i cambi VERDE/SPENTO
                if (buffer[i] == "VERDE" and buffer[i + 1] == "SPENTO") or \
                        (buffer[i] == "SPENTO" and buffer[i + 1] == "VERDE"):
                    transitions += 1

            if transitions >= MIN_TRANSITIONS_FOR_BLINK:
                # Ci sono abbastanza cambi -> È un VERO LAMPEGGIO
                return "ATTESA"
            else:
                # Non ci sono abbastanza cambi -> È una TRANSIZIONE
                # Decide lo stato in base a cosa c'è di più nel buffer
                if verde_count > spento_count:
                    return "VERDE"
                else:
                    return "SPENTO"

        else:
            # Non è un candidato al lampeggio (è VERDE solido)
            return "VERDE"

    # REGOLA 3: Se non c'è né ROSSO né VERDE, è SPENTO
    return "SPENTO"


# --- Funzioni MQTT ---

def on_connect(client, userdata, flags, rc, properties):
    if rc == 0:
        # Aggiungiamo 'flags' per capire se è una nuova connessione o una riconnessione
        print(f"✅ Connesso al broker MQTT! (Flags: {flags}, RC: {rc})")
    else:
        print(f"❌ Connessione MQTT fallita, codice: {rc}.")


def on_disconnect(client, userdata, flags, reason_code, properties):
    # Diamo un log più dettagliato
    print(f"⚠️ Disconnesso dal broker MQTT. Reason code: {reason_code}")
    if reason_code != 0:
        print("   Tentativo di riconnessione automatica gestito da Paho-MQTT...")


# --- Ciclo Principale ---

def main():
    sensor = inizializza_sensore()
    calibrated_data = carica_calibrazione()
    if not sensor or not calibrated_data:
        print("Impossibile avviare. Controlla hardware e configurazione.")
        return

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MACHINE_ID)
    client.on_connect, client.on_disconnect = on_connect, on_disconnect
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    # Aggiungiamo un delay di riconnessione esplicito
    # Inizia dopo 1 secondo, raddoppia ad ogni fallimento, fino a 30 secondi
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
    except Exception as e:
        print(f"❌ Errore di connessione MQTT iniziale: {e}")
        return

    stato_pubblicato = None
    last_published_change_time = 0
    visual_state_buffer = deque(maxlen=BUFFER_SIZE)

    # Inizializza il buffer con "SPENTO"
    for _ in range(BUFFER_SIZE): visual_state_buffer.append("SPENTO")

    print("Avvio monitoraggio...")  # Messaggio di avvio

    try:
        while True:
            # --- Lettura e Analisi ---
            stato_corrente, rgb_corrente = get_instant_status(sensor, calibrated_data)
            visual_state_buffer.append(stato_corrente)

            stato_composito = analyze_state_buffer(visual_state_buffer)

            # --- Logica di Pubblicazione ---
            stato_da_pubblicare = None
            if stato_composito != "SPENTO":
                # Se lo stato è ROSSO, VERDE o ATTESA, pubblicalo subito
                stato_da_pubblicare = stato_composito
                # Aggiorna il timer solo se lo stato *cambia*
                if stato_composito != stato_pubblicato:
                    last_published_change_time = time.time()
            else:
                # Se lo stato è SPENTO, applica la persistenza
                if time.time() - last_published_change_time > STATE_PERSISTENCE_SECONDS:
                    stato_da_pubblicare = "SPENTO"
                else:
                    # Non è passato abbastanza tempo, mantieni l'ultimo stato pubblicato
                    stato_da_pubblicare = stato_pubblicato

            if stato_da_pubblicare != stato_pubblicato:
                stato_pubblicato = stato_da_pubblicare
                # Aggiorna il timer solo quando pubblichiamo un *cambio* reale
                last_published_change_time = time.time()

                timestamp = time.time()
                # Usiamo 'datetime_str' sia per il payload che per la stampa
                datetime_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))

                payload = json.dumps({
                    "stato": stato_pubblicato, "machine_id": MACHINE_ID,
                    "timestamp": timestamp, "datetime_str": datetime_str
                })
                client.publish(MQTT_TOPIC_STATUS, payload, qos=1, retain=True)

                # Stampa il log con il timestamp
                print(f"[{datetime_str}] Stato Pubblicato: {stato_pubblicato}. Invio messaggio MQTT...")

            time.sleep(LOOP_SLEEP_TIME)

    except KeyboardInterrupt:
        print("\n🛑 Chiusura del programma...")
    finally:
        print("🧹 Rilascio risorse...")
        client.loop_stop()
        client.disconnect()
        print("✅ Programma terminato.")


if __name__ == "__main__":
    main()

