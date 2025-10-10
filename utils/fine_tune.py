import cv2
import numpy as np
import json
import os
import time

# --- CONFIGURAZIONE ---
CAMERA_INDEX = 0
WINDOW_NAME_LIVE = "Affinamento Live (s = Salva, q = Esci)"
WINDOW_NAME_SLIDERS = "Regola Soglie %"

# Percorsi
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "..", "config")
ROI_CONFIG_FILE = os.path.join(CONFIG_DIR, "roi_semaforo.json")
COLOR_CONFIG_FILE = os.path.join(CONFIG_DIR, "color_ranges.json")


def load_config(file_path, config_name):
    if not os.path.exists(file_path):
        print(f"❌ Errore: File di configurazione '{config_name}' non trovato.")
        return None
    with open(file_path, 'r') as f:
        return json.load(f)


def save_config(file_path, data):
    """Salva i dati di configurazione aggiornati in formato JSON."""
    try:
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"✅ Configurazione salvata con successo in '{file_path}'")
        return True
    except Exception as e:
        print(f"❌ Errore durante il salvataggio della configurazione: {e}")
        return False


def on_trackbar(val):
    """Funzione placeholder richiesta da OpenCV per i trackbar."""
    pass


def draw_debug_overlay(frame, details, roi_coords):
    """Disegna le informazioni di debug direttamente sul frame video (copiata da monitor_semaforo.py)."""
    # Disegna il rettangolo della ROI
    x, y, w, h = roi_coords['x'], roi_coords['y'], roi_coords['w'], roi_coords['h']
    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
    cv2.putText(frame, "Campo Visivo", (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

    # Scrivi le percentuali di rilevamento
    y_offset = 30
    for name, data in details.items():
        perc = data['percentage']
        thresh = data['threshold']
        text = f"{name}: {perc:.1f}% (>{thresh}%)"
        # Il colore del testo cambia in base al superamento della soglia
        color = (0, 255, 0) if perc >= thresh else (0, 0, 255)

        (text_width, text_height), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        cv2.rectangle(frame, (5, y_offset - text_height - 5), (10 + text_width, y_offset + 5), (0, 0, 0), -1)
        cv2.putText(frame, text, (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1, cv2.LINE_AA)
        y_offset += 25


def main():
    roi = load_config(ROI_CONFIG_FILE, "ROI")
    color_ranges = load_config(COLOR_CONFIG_FILE, "Colori")
    if not roi or not color_ranges:
        print("Esegui prima configura_zona.py e calibra_colori.py.")
        return

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("❌ Errore: Impossibile accedere alla webcam.")
        return

    # Crea le finestre
    cv2.namedWindow(WINDOW_NAME_LIVE)
    cv2.namedWindow(WINDOW_NAME_SLIDERS)

    # Crea uno slider per ogni stato nel file di configurazione
    for state_name, data in color_ranges.items():
        initial_threshold = data.get("threshold_percent", 10)
        cv2.createTrackbar(f'Soglia {state_name}', WINDOW_NAME_SLIDERS, initial_threshold, 100, on_trackbar)

    print("🚀 Avvio strumento di affinamento soglie...")
    print("   -> Regola gli slider e osserva l'effetto sul video.")
    print("   -> Premi 's' per salvare le nuove soglie e uscire.")
    print("   -> Premi 'q' per uscire senza salvare.")

    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue

        x, y, w, h = roi['x'], roi['y'], roi['w'], roi['h']
        roi_frame = frame[y:y + h, x:x + w]

        if roi_frame.size == 0: continue

        hsv_frame = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2HSV)
        total_pixels = roi_frame.shape[0] * roi_frame.shape[1]

        detection_details = {}

        for color_name, ranges in color_ranges.items():
            # Leggi il valore attuale dello slider AD OGNI FRAME
            current_threshold = cv2.getTrackbarPos(f'Soglia {color_name}', WINDOW_NAME_SLIDERS)

            lower = np.array(ranges['lower'])
            upper = np.array(ranges['upper'])
            mask = cv2.inRange(hsv_frame, lower, upper)
            percentage = (cv2.countNonZero(mask) / total_pixels) * 100

            # Memorizza i dati per l'overlay, usando la soglia live dallo slider
            detection_details[color_name] = {'percentage': percentage, 'threshold': current_threshold}

        # Disegna l'overlay usando i dati appena calcolati
        draw_debug_overlay(frame.copy(), detection_details, roi)
        cv2.imshow(WINDOW_NAME_LIVE, frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            print("🛑 Uscita senza salvare.")
            break
        elif key == ord('s'):
            print("💾 Salvataggio delle nuove soglie...")
            # Aggiorna il dizionario con i nuovi valori finali dagli slider
            for color_name in color_ranges.keys():
                new_threshold = cv2.getTrackbarPos(f'Soglia {color_name}', WINDOW_NAME_SLIDERS)
                color_ranges[color_name]['threshold_percent'] = new_threshold

            save_config(COLOR_CONFIG_FILE, color_ranges)
            break

    cap.release()
    cv2.destroyAllWindows()
    print("✅ Strumento di affinamento terminato.")


if __name__ == "__main__":
    main()

