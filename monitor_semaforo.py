import cv2
import numpy as np
import paho.mqtt.client as mqtt
import json
import time
import os
import argparse

# --- COSTRUZIONE DINAMICA DEI PERCORSI ---
# Questo rende lo script eseguibile dalla cartella radice del progetto
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROI_CONFIG_FILE = os.path.join(SCRIPT_DIR, "config", "roi_semaforo.json")
COLOR_CONFIG_FILE = os.path.join(SCRIPT_DIR, "config", "color_ranges.json")

# --- CONFIGURAZIONE GENERALE ---
CAMERA_INDEX = 0
MACHINE_ID = "macchina_01"

# --- CONFIGURAZIONE MQTT (IMPOSTAZIONI BMA) ---
MQTT_BROKER = "192.168.20.163"
MQTT_PORT = 1883
MQTT_USERNAME = "shima"
MQTT_PASSWORD = "shima"
MQTT_TOPIC = f"bma/{MACHINE_ID}/semaforo/stato"


def load_roi():
    """Carica le coordinate della ROI dal file JSON."""
    if not os.path.exists(ROI_CONFIG_FILE):
        print(f"❌ Errore: File di configurazione '{ROI_CONFIG_FILE}' non trovato.")
        print("➡️  Esegui prima lo script 'utils/configura_zona.py'!")
        return None
    with open(ROI_CONFIG_FILE, 'r') as f:
        return json.load(f)


def load_color_ranges():
    """Carica i range di colori calibrati dal file JSON."""
    if not os.path.exists(COLOR_CONFIG_FILE):
        print(f"❌ Errore: File di configurazione colori '{COLOR_CONFIG_FILE}' non trovato.")
        print("➡️  Esegui prima lo script 'utils/calibra_colori.py'!")
        return None
    with open(COLOR_CONFIG_FILE, 'r') as f:
        data = json.load(f)
        # Converte le liste JSON in tuple per l'uso con OpenCV
        color_ranges = {color: (data[color]['lower'], data[color]['upper']) for color in data}
        print("✅ Range di colori caricati con successo dal file di configurazione.")
        return color_ranges


def on_connect(client, userdata, flags, reason_code, properties):
    """Callback eseguita alla connessione con il broker."""
    if reason_code == 0:
        print("✅ Connesso con successo al broker MQTT!")
    else:
        print(f"❌ Connessione fallita, codice: {reason_code}. Controlla IP, porta, utente e password.")


def on_disconnect(client, userdata, reason_code, properties):
    """Callback eseguita in caso di disconnessione."""
    if reason_code != 0:
        print(f"🔌 Disconnessione inaspettata dal broker MQTT! Codice: {reason_code}")


def main(args):
    """Loop principale di monitoraggio."""
    roi = load_roi()
    color_ranges = load_color_ranges()

    # Se uno dei due file di configurazione manca, il programma non può partire.
    if not roi or not color_ranges:
        return

    # Inizializza e connetti il client MQTT
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MACHINE_ID)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    try:
        print(f"🔗 Tentativo di connessione al broker MQTT: {MQTT_BROKER}...")
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()  # Gestisce la connessione in un thread separato
    except Exception as e:
        print(f"❌ Errore critico di connessione MQTT: {e}")
        return

    # Inizializza la webcam
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("❌ Errore: Impossibile accedere alla webcam.")
        return

    print("🚀 Avvio monitoraggio semaforo... (Premi Ctrl+C per fermare)")
    if args.debug:
        print("   -> 🔎 MODALITÀ DEBUG ATTIVA. Premi 'q' nella finestra video per uscire.")

    stato_precedente = None

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("⚠️ Frame non ricevuto, riprovo...")
                time.sleep(1)
                continue

            # Estrai la regione di interesse (ROI)
            x, y, w, h = roi['x'], roi['y'], roi['w'], roi['h']
            roi_frame = frame[y:y + h, x:x + w]

            # Converti in spazio colore HSV per un rilevamento più robusto
            hsv_frame = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2HSV)

            stato_corrente = "SPENTO"
            max_pixels = 100  # Soglia minima di pixel per considerare un colore attivo

            combined_mask = np.zeros(hsv_frame.shape[:2], dtype="uint8")

            # Itera sui colori caricati dal file JSON
            for color_name, (lower, upper) in color_ranges.items():
                mask = cv2.inRange(hsv_frame, np.array(lower), np.array(upper))

                if args.debug:
                    combined_mask = cv2.bitwise_or(combined_mask, mask)

                pixel_count = cv2.countNonZero(mask)
                if pixel_count > max_pixels:
                    stato_corrente = color_name
                    # Non usciamo subito dal ciclo per gestire il caso in cui più colori
                    # siano rilevati; vincerà quello con più pixel.
                    max_pixels = pixel_count

            # Pubblica su MQTT solo se lo stato è cambiato
            if stato_corrente != stato_precedente:
                print(f"\nStato cambiato: {stato_precedente} -> {stato_corrente}. Invio messaggio MQTT...")
                payload = json.dumps({"stato": stato_corrente, "timestamp": int(time.time())})
                client.publish(MQTT_TOPIC, payload, qos=1, retain=True)
                stato_precedente = stato_corrente

            # Sezione per mostrare le finestre di debug
            if args.debug:
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                cv2.imshow("Live Feed con ROI", frame)
                cv2.imshow("ROI Analizzata", roi_frame)
                cv2.imshow("Maschera Colore Rilevato", combined_mask)

                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    break
            else:
                # In modalità normale, una piccola pausa per non caricare la CPU al 100%
                time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n Arresto del programma richiesto dall'utente.")
    finally:
        # Blocco di pulizia per chiudere tutto correttamente
        print("🧹 Pulizia e chiusura delle risorse...")
        cap.release()
        if args.debug:
            cv2.destroyAllWindows()
        client.loop_stop()
        client.disconnect()
        print("Arrivederci!")


if __name__ == "__main__":
    # Logica per leggere gli argomenti da riga di comando (es. --debug)
    parser = argparse.ArgumentParser(description="Monitora lo stato di un semaforo via webcam e invia dati via MQTT.")
    parser.add_argument("--debug", action="store_true", help="Abilita la modalità debug con finestre video.")
    args = parser.parse_args()
    main(args)

