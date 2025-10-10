import cv2
import numpy as np
import json
import os
import time

# --- CONFIGURAZIONE ---
CAMERA_INDEX = 0
# AGGIUNTO: Ora calibriamo anche lo stato SPENTO per gestire meglio le luci a torre
STATES_TO_CALIBRATE = ["ROSSO", "VERDE", "SPENTO"]
RECORDING_SECONDS = 5  # Durata della registrazione per ogni campione
ANALYSIS_FRAME_COUNT = 30  # Numero di frame da analizzare dal video per la statistica

# Percorsi
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "..", "config")
COLOR_CONFIG_FILE = os.path.join(CONFIG_DIR, "color_ranges.json")
ROI_CONFIG_FILE = os.path.join(CONFIG_DIR, "roi_semaforo.json")
TEMP_VIDEO_FILE = os.path.join(SCRIPT_DIR, "temp_sample.avi")


def load_roi():
    if not os.path.exists(ROI_CONFIG_FILE):
        print(f"❌ Errore: File ROI '{ROI_CONFIG_FILE}' non trovato. Esegui prima configura_zona.py.")
        return None
    with open(ROI_CONFIG_FILE, 'r') as f:
        return json.load(f)


def record_sample_video(roi_coords):
    """Registra un video di N secondi mostrando solo la ROI."""
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("❌ Errore: Impossibile accedere alla webcam.")
        return False

    # Ottieni proprietà video per il salvataggio
    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = int(cap.get(cv2.CAP_PROP_FPS))
    if fps == 0: fps = 20  # Fallback per alcune webcam

    # Codec per il video temporaneo
    fourcc = cv2.VideoWriter_fourcc(*'XVID')
    out = cv2.VideoWriter(TEMP_VIDEO_FILE, fourcc, fps, (frame_width, frame_height))

    print(f"    -> Inizio registrazione per {RECORDING_SECONDS} secondi...")
    start_time = time.time()
    while (time.time() - start_time) < RECORDING_SECONDS:
        ret, frame = cap.read()
        if not ret: break

        # Disegna un rettangolo per guidare l'utente
        x, y, w, h = roi_coords['x'], roi_coords['y'], roi_coords['w'], roi_coords['h']
        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 255), 2)
        cv2.putText(frame, "REGISTRAZIONE IN CORSO...", (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)

        out.write(frame)
        cv2.imshow("Registrazione...", frame)
        cv2.waitKey(1)

    print("    -> Registrazione completata.")
    cap.release()
    out.release()
    cv2.destroyAllWindows()
    return True


def analyze_video_sample(roi_coords):
    """Permette all'utente di selezionare un'area nel video e ne calcola il range HSV."""
    cap = cv2.VideoCapture(TEMP_VIDEO_FILE)
    if not cap.isOpened():
        print(f"❌ Errore: Impossibile aprire il video campione '{TEMP_VIDEO_FILE}'.")
        return None

    # Estrai frame casuali dal video per l'analisi
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_indices = np.random.choice(total_frames, min(total_frames, ANALYSIS_FRAME_COUNT), replace=False)

    frames_to_analyze = []
    for i in frame_indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ret, frame = cap.read()
        if ret:
            frames_to_analyze.append(frame)

    cap.release()
    if not frames_to_analyze:
        print("❌ Errore: Nessun frame valido estratto dal video.")
        return None

    # Permetti all'utente di selezionare la sotto-area di interesse sul primo frame
    first_frame = frames_to_analyze[0].copy()
    x, y, w, h = roi_coords['x'], roi_coords['y'], roi_coords['w'], roi_coords['h']
    cv2.putText(first_frame, "Disegna un rettangolo SULLA LUCE e premi INVIO", (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (0, 0, 255), 2)
    selection = cv2.selectROI("Seleziona l'area del colore dal campione", first_frame)
    cv2.destroyAllWindows()

    if selection[2] == 0 or selection[3] == 0:
        print("    -> Selezione annullata.")
        return None

    # Estrai i dati HSV da tutti i frame, ma solo dall'area selezionata
    hsv_data = []
    for frame in frames_to_analyze:
        sel_x, sel_y, sel_w, sel_h = selection
        selected_area = frame[sel_y:sel_y + sel_h, sel_x:sel_x + sel_w]
        if selected_area.size == 0: continue

        hsv_area = cv2.cvtColor(selected_area, cv2.COLOR_BGR2HSV)
        # Reshape per avere una lista di pixel [H, S, V]
        pixels = hsv_area.reshape(-1, 3)
        hsv_data.extend(pixels)

    if not hsv_data:
        print("❌ Errore: Nessun pixel valido trovato nell'area selezionata.")
        return None

    hsv_data = np.array(hsv_data)

    # Calcola statistiche (media e deviazione standard)
    mean = np.mean(hsv_data, axis=0)
    std = np.std(hsv_data, axis=0)

    # Calcola i range. Usiamo 2 deviazioni standard per coprire il 95% dei valori.
    # Aggiungiamo un piccolo buffer per la tonalità.
    lower_bound = np.maximum(0, mean - std * 2).astype(int)
    upper_bound = np.minimum([179, 255, 255], mean + std * 2).astype(int)

    print(f"    -> Analisi completata. Range calcolato: {lower_bound.tolist()} -> {upper_bound.tolist()}")
    return {"lower": lower_bound.tolist(), "upper": upper_bound.tolist()}


def main():
    roi = load_roi()
    if not roi: return

    calibrated_data = {}
    print("--- Inizio Calibrazione Colori Basata su Campioni Video ---")

    for state_name in STATES_TO_CALIBRATE:
        print(f"\n[FASE: {state_name}]")
        input(
            f"    -> Prepara la macchina per mostrare lo stato '{state_name}'. Premi INVIO per avviare la registrazione...")

        if not record_sample_video(roi):
            print("    -> Errore durante la registrazione. Annullamento.")
            return

        print("    -> Ora seleziona l'area esatta della luce dal video campione.")
        time.sleep(1)  # Pausa per permettere all'utente di prepararsi

        color_range = analyze_video_sample(roi)

        if color_range:
            calibrated_data[state_name] = color_range
        else:
            print(f"    -> Calibrazione per lo stato '{state_name}' saltata.")

    # Pulizia del file video temporaneo
    if os.path.exists(TEMP_VIDEO_FILE):
        os.remove(TEMP_VIDEO_FILE)

    if len(calibrated_data) < len(STATES_TO_CALIBRATE):
        print("\n❌ Calibrazione incompleta. Nessun file di configurazione è stato salvato.")
        return

    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(COLOR_CONFIG_FILE, 'w') as f:
        json.dump(calibrated_data, f, indent=4)

    print(f"\n\n🎉 Calibrazione completata! Valori salvati in '{COLOR_CONFIG_FILE}'.")


if __name__ == "__main__":
    main()

