import cv2
import numpy as np
import paho.mqtt.client as mqtt
import json
import time
import os

# --- CONFIGURAZIONE ---
CAMERA_INDEX = 0
ROI_CONFIG_FILE = "config/roi_semaforo.json"

# --- CONFIGURAZIONE MQTT ---
MQTT_BROKER = "192.168.20.63"  # ⬅️ SOSTITUISCI CON L'IP DEL TUO BROKER
MQTT_PORT = 1883
# Il Client ID dovrebbe essere unico per ogni macchina
MACHINE_ID = "macchina_01"
MQTT_TOPIC = f"bma/{MACHINE_ID}/semaforo/stato"

# --- DEFINIZIONE COLORI IN HSV ---
# Questi valori potrebbero necessitare di aggiustamenti
COLOR_RANGES = {
    "ROSSO": ([0, 120, 70], [10, 255, 255]),
    "GIALLO": ([20, 100, 100], [30, 255, 255]),
    "VERDE": ([40, 70, 70], [80, 255, 255])
}


def load_roi():
    """Carica le coordinate della ROI dal file JSON."""
    if not os.path.exists(ROI_CONFIG_FILE):
        print(f"❌ Errore: File di configurazione '{ROI_CONFIG_FILE}' non trovato.")
        print("➡️ Esegui prima lo script 'configura_zona.py'!")
        return None
    with open(ROI_CONFIG_FILE, 'r') as f:
        return json.load(f)


def on_connect(client, userdata, flags, rc):
    """Callback per la connessione MQTT."""
    if rc == 0:
        print("✅ Connesso al broker MQTT!")
    else:
        print(f"❌ Connessione fallita, codice: {rc}")


def main():
    """Loop principale di monitoraggio."""
    roi = load_roi()
    if not roi:
        return

    # Inizializza client MQTT
    client = mqtt.Client(client_id=MACHINE_ID)
    client.on_connect = on_connect
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()  # Gestisce la riconnessione in background
    except Exception as e:
        print(f"❌ Errore di connessione MQTT: {e}")
        return

    # Inizializza webcam
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("❌ Errore: Impossibile accedere alla webcam.")
        return

    print("🚀 Avvio monitoraggio semaforo...")
    stato_precedente = None

    while True:
        ret, frame = cap.read()
        if not ret:
            print("⚠️ Frame non ricevuto, riprovo...")
            time.sleep(1)
            continue

        # Estrai la ROI dal frame
        x, y, w, h = roi['x'], roi['y'], roi['w'], roi['h']
        roi_frame = frame[y:y + h, x:x + w]

        # Converti in spazio colore HSV (più robusto ai cambi di luce)
        hsv_frame = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2HSV)

        stato_corrente = "SPENTO"
        max_pixels = 100  # Soglia minima per considerare un colore attivo

        for color_name, (lower, upper) in COLOR_RANGES.items():
            lower_bound = np.array(lower)
            upper_bound = np.array(upper)

            # Crea una maschera per il colore corrente
            mask = cv2.inRange(hsv_frame, lower_bound, upper_bound)

            # Conta i pixel che corrispondono al colore
            pixel_count = cv2.countNonZero(mask)

            if pixel_count > max_pixels:
                stato_corrente = color_name
                max_pixels = pixel_count

        # Se lo stato è cambiato, pubblicalo su MQTT
        if stato_corrente != stato_precedente:
            print(f"Stato cambiato: {stato_precedente} -> {stato_corrente}")
            payload = json.dumps({"stato": stato_corrente, "timestamp": time.time()})
            client.publish(MQTT_TOPIC, payload, qos=1)
            stato_precedente = stato_corrente

        # Attesa per ridurre il carico sulla CPU
        time.sleep(0.5)

    # Cleanup
    cap.release()
    client.loop_stop()


if __name__ == "__main__":
    main()