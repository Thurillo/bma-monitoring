#!/usr/bin/env python3
"""
MONITOR SEMAFORO - Versione TCS34725 (4 Stati)

Questo script sostituisce la logica OpenCV con un sensore TCS34725.
Implementa una logica a 4 stati (ROSSO, VERDE, ATTESA, SPENTO) analizzando
un buffer di letture per distinguere tra fisso e lampeggiante.

Versione stabile con output di log su singola riga (con timestamp)
e gestione MQTT non bloccante.

AGGIUNTA: Logica di Coda Offline (Store-and-Forward).
Salva i messaggi su file se la rete non è disponibile e li invia
alla riconnessione.
"""

import time
import json
import sys
import os
import platform
import subprocess
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
# Numero di campioni da mediare per una singola lettura (più alto = più stabile)
CAMPIONI_PER_LETTURA = 1
# Numero di letture da tenere in memoria (più alto = analisi più lunga)
BUFFER_SIZE = 20
# Pausa tra i cicli di lettura E timeout per il loop MQTT
LOOP_SLEEP_TIME = 0.1  # Stabile per MQTT
# Secondi di "SPENTO" prima di pubblicare lo stato SPENTO
STATE_PERSISTENCE_SECONDS = 0.5
# Soglia per il lampeggio: % di letture "SPENTO" nel buffer per definirlo "ATTESA"
BLINK_THRESHOLD_PERCENT = 0.10  # (10%)
# Per essere "ATTESA", il buffer deve avere almeno questo numero di cambi (V->S o S->V)
MIN_TRANSITIONS_FOR_BLINK = 2

# --- CONFIGURAZIONE MQTT (e Percorsi) ---
# Rimossi MACHINE_ID e MQTT_TOPIC_STATUS da qui.
# Verranno caricati dal file JSON.
MQTT_BROKER = "192.168.20.163"
MQTT_PORT = 1883
MQTT_USERNAME = "shima"
MQTT_PASSWORD = "shima"
# --- NUOVA AGGIUNTA: Topic di trigger globale ---
MQTT_TRIGGER_TOPIC = "bma/cambiostato"
# ----------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "config")
CALIBRATION_FILE = os.path.join(CONFIG_DIR, "calibrazione.json")
# --- NUOVO FILE PER LA CODA OFFLINE ---
OFFLINE_QUEUE_FILE = os.path.join(CONFIG_DIR, "offline_queue.log")

# --- NUOVE VARIABILI GLOBALI PER LA RETE ---
is_network_online = False
is_mqtt_connected = False
PING_HOST = None  # Verrà caricato dalla configurazione
NETWORK_CHECK_INTERVAL = 30  # Secondi tra un ping e l'altro
last_network_check = 0


# ----------------------------------------

# --- Inizializzazione Hardware ---

def inizializza_sensore():
    """Inizializza il sensore TCS34725."""
    print("🔧 Inizializzazione sensore TCS34725...")
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        sensor = adafruit_tcs34725.TCS34725(i2c)
        # Impostazioni per alta sensibilità
        sensor.integration_time = 250
        sensor.gain = 16
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
        # Modifichiamo il controllo per assicurarci che i colori ci siano,
        # l'ID macchina verrà controllato nel main.
        if "verde" not in data or "non_verde" not in data or "buio" not in data:
            print("❌ ERRORE: File di calibrazione incompleto (mancano i colori).")
            print("   Esegui 'utils/calibra_sensore.py' per ricalibrare.")
            return None

        # --- MODIFICA: Controllo PING_HOST ---
        if "ping_host" not in data or not data["ping_host"]:
            print("❌ ERRORE: 'ping_host' non impostato nel file di calibrazione.")
            print("   Esegui 'utils/calibra_sensore.py' e imposta un Host Ping (Opz. 5).")
            return None
        # --- FINE MODIFICA ---

        print(f"✅ Dati di calibrazione caricati da '{CALIBRATION_FILE}'")
        return data
    except Exception as e:
        print(f"❌ ERRORE durante la lettura del file JSON: {e}")
        return None


# --- Funzioni di Lettura e Analisi ---

def leggi_rgb_attuale(sens):
    """Esegue una singola lettura RGB, con fallback."""
    try:
        # Metodo preferito
        result = sens.color_rgb_bytes
        if len(result) >= 3: return result[:3]
    except Exception:
        pass  # Tenta con il metodo raw
    try:
        # Metodo di fallback
        raw = sens.color_raw
        return min(255, int(raw[0] / 256)), min(255, int(raw[1] / 256)), min(255, int(raw[2] / 256))
    except Exception:
        return 0, 0, 0  # Errore grave


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
            pass  # Ignora letture fallite
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

    # Calcola la distanza da ogni stato calibrato
    dist_verde = calcola_distanza_rgb(rgb_medio, calib_data['verde'])
    dist_rosso = calcola_distanza_rgb(rgb_medio, calib_data['non_verde'])
    dist_buio = calcola_distanza_rgb(rgb_medio, calib_data['buio'])

    distanze = {
        "VERDE": dist_verde,
        "ROSSO": dist_rosso,
        "SPENTO": dist_buio
    }

    # Lo stato è quello con la distanza minore
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


# --- NUOVE FUNZIONI: Gestione Rete e Coda Offline ---

def check_network(host):
    """Controlla la connettività di rete pingando un host."""
    try:
        # Determina il parametro ping corretto per il sistema operativo
        param = '-n' if platform.system().lower() == 'windows' else '-c'
        # Costruisce il comando di ping
        command = ['ping', param, '1', host]
        # Esegue il ping, sopprimendo l'output
        response = subprocess.call(command, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        # Ritorna True se il ping ha successo (codice 0), False altrimenti
        return response == 0
    except Exception:
        return False


def save_to_queue(payload_string):
    """Salva un payload (stringa JSON) nella coda offline."""
    try:
        with open(OFFLINE_QUEUE_FILE, 'a') as f:
            f.write(payload_string + '\n')
    except Exception as e:
        print(f"   ⚠️ Errore durante il salvataggio nella coda: {e}")


def process_offline_queue(client, queue_file):
    """Invia i messaggi in coda a MQTT dopo la riconnessione."""
    global MQTT_TRIGGER_TOPIC
    if not os.path.exists(queue_file):
        return  # Nessuna coda da processare

    try:
        with open(queue_file, 'r') as f:
            lines = f.readlines()

        if not lines:
            os.remove(queue_file)  # Rimuovi se vuoto
            return

        print(f"🔄 Trovati {len(lines)} messaggi nella coda offline. Invio in corso...")

        for payload_string in lines:
            payload_string = payload_string.strip()
            if not payload_string:
                continue

            try:
                # Dobbiamo ri-ottenere il topic della macchina dal payload
                payload_json = json.loads(payload_string)
                machine_id = payload_json.get("message", {}).get("machine_id", None)

                if machine_id:
                    topic_status = f"bma/{machine_id}/semaforo/stato"
                    # Pubblica su entrambi i topic, come da logica principale
                    client.publish(topic_status, payload_string, qos=1, retain=True)
                    client.publish(MQTT_TRIGGER_TOPIC, payload_string, qos=1, retain=True)
                    time.sleep(0.1)  # Evita di sovraccaricare il broker
                else:
                    print(f"   ⚠️ Messaggio in coda corrotto (manca machine_id): {payload_string[:50]}...")

            except Exception as e:
                print(f"   ⚠️ Errore nell'invio del messaggio in coda: {e}")

        # Dopo aver inviato tutto, svuota il file
        with open(queue_file, 'w') as f:
            f.write('')
        print(f"✅ Coda offline svuotata. Sincronizzazione completata.")

    except Exception as e:
        print(f"   ⚠️ Errore critico nel processare la coda offline: {e}")
        # Non eliminiamo il file se c'è stato un errore


# --- Funzioni MQTT (Modificate) ---

def on_connect(client, userdata, flags, rc, properties):
    """Callback per quando ci si connette al broker."""
    global is_mqtt_connected
    if rc == 0:
        print(f"✅ Connesso al broker MQTT! (Flags: {flags}, RC: {rc})")
        is_mqtt_connected = True

        # --- NUOVO: Processa la coda dopo la connessione ---
        queue_file = userdata.get("queue_file")
        if queue_file:
            process_offline_queue(client, queue_file)
        # --- FINE NUOVO ---
    else:
        print(f"❌ Connessione MQTT fallita, codice: {rc}.")
        is_mqtt_connected = False


def on_disconnect(client, userdata, flags, reason_code, properties):
    """Callback per quando ci si disconnette."""
    global is_mqtt_connected
    print(f"⚠️ Disconnesso dal broker MQTT. Reason code: {reason_code}")
    is_mqtt_connected = False
    if reason_code != 0:
        print("   Tentativo di riconnessione automatica gestito da Paho-MQTT...")


# --- Ciclo Principale ---

def main():
    # --- MODIFICA: Spostate variabili globali ---
    global PING_HOST, last_network_check, is_network_online, is_mqtt_connected
    # --- FINE MODIFICA ---

    sensor = inizializza_sensore()
    calibrated_data = carica_calibrazione()
    if not sensor or not calibrated_data:
        print("Impossibile avviare. Controlla hardware e configurazione.")
        return

    # --- CARICAMENTO DINAMICO MACHINE_ID ---
    # Carica l'ID Macchina e imposta il Topic MQTT
    if "machine_id" not in calibrated_data or not calibrated_data["machine_id"]:
        print(f"❌ ERRORE: 'machine_id' non trovato o non impostato in '{CALIBRATION_FILE}'.")
        print(f"   Esegui 'utils/calibra_sensore.py' e imposta un ID Macchina (Opzione 4).")
        return

    MACHINE_ID = calibrated_data["machine_id"]
    MQTT_TOPIC_STATUS = f"bma/{MACHINE_ID}/semaforo/stato"
    # --- NUOVO: Carica PING_HOST ---
    PING_HOST = calibrated_data["ping_host"]
    print(f"✅ ID Macchina caricato: {MACHINE_ID} (Topic: {MQTT_TOPIC_STATUS})")
    print(f"✅ Host di Rete caricato: {PING_HOST}")
    # --- FINE NUOVO ---

    # --- FASE DI INIZIALIZZAZIONE BUFFER ---
    # Riempe il buffer con letture reali per evitare uno stato iniziale errato.
    print(f"Avvio... (Inizializzazione buffer... {BUFFER_SIZE} letture)")
    visual_state_buffer = deque(maxlen=BUFFER_SIZE)

    for i in range(BUFFER_SIZE):
        stato_iniziale, _ = get_instant_status(sensor, calibrated_data)
        visual_state_buffer.append(stato_iniziale)
        print(f"   Lettura... {i + 1}/{BUFFER_SIZE} -> {stato_iniziale}   ", end="\r")
        # Applica una pausa per non sovraccaricare il sensore all'avvio
        time.sleep(LOOP_SLEEP_TIME)

    print("\n✅ Inizializzazione completata.")

    # Inizializzazione client MQTT
    # L'ID client ORA è dinamico
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MACHINE_ID)
    client.on_connect, client.on_disconnect = on_connect, on_disconnect
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    # --- NUOVO: Aggiungi il percorso della coda a userdata ---
    client.user_data_set({"queue_file": OFFLINE_QUEUE_FILE})
    # --- FINE NUOVO ---

    # --- MODIFICA: Non connettere subito, lascia che sia il loop a farlo ---
    # try:
    #     client.connect(MQTT_BROKER, MQTT_PORT, 60)
    # except Exception as e:
    #     print(f"❌ Errore di connessione MQTT iniziale: {e}")
    #     return
    # --- FINE MODIFICA ---

    stato_pubblicato = None
    last_published_change_time = 0

    print("Monitoraggio attivo.")

    try:
        while True:
            # --- NUOVO BLOCCO: CONTROLLO RETE E CONNESSIONE MQTT ---
            current_time = time.time()
            if (current_time - last_network_check) > NETWORK_CHECK_INTERVAL:
                last_network_check = current_time
                was_online = is_network_online
                is_network_online = check_network(PING_HOST)

                if is_network_online and not is_mqtt_connected:
                    # Rete tornata, prova a connettere MQTT
                    if not was_online:  # Stampa solo al cambio
                        print(
                            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ Rete Rilevata. Connessione a MQTT...")
                    try:
                        # Paho gestirà la riconnessione in background
                        client.connect(MQTT_BROKER, MQTT_PORT, 60)
                    except Exception as e:
                        # Gestisce il caso in cui il client è già "connecting"
                        if "Connection refused" not in str(e):
                            print(f"   Errore tentativo connessione: {e}")

                if not is_network_online and is_mqtt_connected:
                    # Rete persa, forza disconnessione MQTT
                    print(
                        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ❌ Rete Assente. Disconnessione forzata da MQTT...")
                    client.disconnect()  # Questo triggera on_disconnect

            # --- FINE BLOCCO RETE ---

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
                datetime_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))

                # --- MODIFICA CORRETTIVA ---
                # 1. Crea il dizionario dati (come prima)
                payload_data = {
                    "stato": stato_pubblicato, "machine_id": MACHINE_ID,
                    "timestamp": timestamp, "datetime_str": datetime_str
                }
                # 2. Annida i dati dentro l'oggetto "message" come richiesto da n8n
                payload = json.dumps({"message": payload_data})
                # --- FINE MODIFICA CORRETTIVA ---

                # --- MODIFICA LOGICA: Pubblica o Salva in Coda ---
                if is_mqtt_connected:
                    # 1. Pubblica lo stato completo (ora annidato) sul topic della macchina
                    client.publish(MQTT_TOPIC_STATUS, payload, qos=1, retain=True)

                    # 2. Pubblica lo STESSO payload completo sul topic di trigger
                    client.publish(MQTT_TRIGGER_TOPIC, payload, qos=1, retain=True)

                    # Stampa il log con il timestamp
                    print(
                        f"[{datetime_str}] Stato Pubblicato: {stato_pubblicato}. Invio trigger a '{MQTT_TRIGGER_TOPIC}'...")
                else:
                    # Se MQTT non è connesso, salva in coda
                    save_to_queue(payload)
                    print(f"[{datetime_str}] MQTT OFFLINE. Stato {stato_pubblicato} salvato in coda.")
                # --- FINE MODIFICA ---

            # --- MODIFICA CRITICA per MQTT ---
            # Gestisce la rete (incluso il keepalive) E funge da pausa.
            # Sostituisce client.loop_start() e time.sleep()
            client.loop(timeout=LOOP_SLEEP_TIME)

    except KeyboardInterrupt:
        print("\n🛑 Chiusura del programma...")
    finally:
        print("🧹 Rilascio risorse...")
        client.disconnect()
        print("✅ Programma terminato.")


if __name__ == "__main__":
    main()


