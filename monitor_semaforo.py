import cv2
import numpy as np
import paho.mqtt.client as mqtt
import json
import time
import os

# --- COSTRUZIONE DINAMICA DEL PERCORSO ---
# Trova il percorso assoluto dello script attuale (.../bma-monitoring/monitor_semaforo.py)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Costruisce il percorso corretto per il file di configurazione
ROI_CONFIG_FILE = os.path.join(SCRIPT_DIR, "config", "roi_semaforo.json")

# --- CONFIGURAZIONE GENERALE ---
CAMERA_INDEX = 0
MACHINE_ID = "macchina_01"  # CAMBIA QUESTO ID PER OGNI MACCHINA

# --- CONFIGURAZIONE MQTT (IMPOSTAZIONI BMA) ---
MQTT_BROKER = "192.168.20.163"
MQTT_PORT = 1883  # La porta standard è 1883, hai scritto 1833, verifica se è un refuso.
MQTT_USERNAME = "shima"
MQTT_PASSWORD = "shima"
MQTT_TOPIC = f"bma/{MACHINE_ID}/semaforo/stato"

# --- DEFINIZIONE COLORI IN HSV ---
# Questi valori potrebbero necessitare di aggiustamenti in base alla luce ambientale
COLOR_RANGES = {
    "ROSSO": ([0, 120, 70], [10, 255, 255]),
    "GIALLO": ([20, 100, 100], [30, 255, 255]),
    "VERDE": ([40, 70, 70], [80, 255, 255])
}


def load_roi():
    """Carica le coordinate della ROI dal file JSON."""
    if not os.path.exists(ROI_CONFIG_FILE):
        print(f"❌ Errore: File di configurazione '{ROI_CONFIG_FILE}' non trovato.")
        print("➡️  Esegui prima lo script 'utils/configura_zona.py'!")
        return None
    with open(ROI_CONFIG_FILE, 'r') as f:
        return json.load(f)


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print("✅ Connesso con successo al broker MQTT!")
    else:
        print(f"❌ Connessione fallita, codice: {reason_code}. Controlla IP, porta, utente e password.")


def on_disconnect(client, userdata, reason_code, properties):
    if reason_code != 0:
        print(f"🔌 Disconnessione inaspettata dal broker MQTT! Codice: {reason_code}")


def main():
    roi = load_roi()
    if not roi:
        return

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MACHINE_ID)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    try:
        print(f"🔗 Tentativo di connessione al broker MQTT: {MQTT_BROKER}...")
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
    except Exception as e:
        print(f"❌ Errore critico di connessione MQTT: {e}")
        return

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("❌ Errore: Impossibile accedere alla webcam.")
        return

    print("🚀 Avvio monitoraggio semaforo... (Premi Ctrl+C per fermare)")
    stato_precedente = None

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                time.sleep(1)
                continue

            x, y, w, h = roi['x'], roi['y'], roi['w'], roi['h']
            roi_frame = frame[y:y + h, x:x + w]
            hsv_frame = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2HSV)
            stato_corrente = "SPENTO"
            max_pixels = 100

            for color_name, (lower, upper) in COLOR_RANGES.items():
                mask = cv2.inRange(hsv_frame, np.array(lower), np.array(upper))
                pixel_count = cv2.countNonZero(mask)
                if pixel_count > max_pixels:
                    stato_corrente = color_name
                    max_pixels = pixel_count

            if stato_corrente != stato_precedente:
                print(f"\nStato cambiato: {stato_precedente} -> {stato_corrente}. Invio messaggio MQTT...")
                payload = json.dumps({"stato": stato_corrente, "timestamp": int(time.time())})
                client.publish(MQTT_TOPIC, payload, qos=1, retain=True)
                stato_precedente = stato_corrente

            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n Arresto del programma richiesto dall'utente.")
    finally:
        print("🧹 Pulizia e chiusura delle risorse...")
        cap.release()
        client.loop_stop()
        client.disconnect()
        print("Arrivederci!")


if __name__ == "__main__":
    main()