import cv2
import numpy as np
import json
import os
import time

# --- CONFIGURAZIONE ---
CAMERA_INDEX = 0
STATES_TO_CALIBRATE = ["ROSSO", "VERDE", "SPENTO"]
RECORDING_SECONDS = 5
ANALYSIS_FRAME_COUNT = 30

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


def draw_text_with_background(frame, text, position, font_scale=0.6, color=(255, 255, 255), bg_color=(0, 0, 0)):
    """Disegna del testo con uno sfondo per una migliore leggibilità."""
    (text_width, text_height), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)
    x, y = position
    cv2.rectangle(frame, (x, y - text_height - baseline), (x + text_width, y), bg_color, -1)
    cv2.putText(frame, text, (x, y - baseline), cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, 1, cv2.LINE_AA)


def record_and_analyze(state_name, roi_coords):
    """Gestisce il ciclo di registrazione e analisi per un singolo stato."""
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("❌ Errore webcam durante la registrazione.")
        return None

    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = int(cap.get(cv2.CAP_PROP_FPS)) or 20

    fourcc = cv2.VideoWriter_fourcc(*'XVID')
    out = cv2.VideoWriter(TEMP_VIDEO_FILE, fourcc, fps, (frame_width, frame_height))

    start_time = time.time()
    print(f"    -> Inizio registrazione per {state_name}...")
    while True:
        elapsed = time.time() - start_time
        if elapsed > RECORDING_SECONDS:
            break

        ret, frame = cap.read()
        if not ret: break

        # Countdown visivo
        countdown = RECORDING_SECONDS - int(elapsed)
        draw_text_with_background(frame, f"REGISTRAZIONE {state_name}: {countdown}s", (10, 30), color=(0, 0, 255))

        out.write(frame)
        cv2.imshow("Calibrazione", frame)
        cv2.waitKey(1)

    print("    -> Registrazione completata.")
    cap.release()
    out.release()

    # --- FASE DI ANALISI ---
    cap = cv2.VideoCapture(TEMP_VIDEO_FILE)
    ret, first_frame = cap.read()
    if not ret:
        print("❌ Errore: impossibile leggere il video campione.")
        return None

    draw_text_with_background(first_frame, "Disegna un rettangolo SULLA LUCE e premi INVIO", (10, 60),
                              color=(0, 255, 255))
    selection = cv2.selectROI("Calibrazione: Seleziona Campione Puro", first_frame)
    cv2.destroyWindow("Calibrazione: Seleziona Campione Puro")

    if selection[2] == 0 or selection[3] == 0:
        print("    -> Selezione annullata.")
        return None

    # Estrai i dati HSV dal video
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_indices = np.random.choice(total_frames, min(total_frames, ANALYSIS_FRAME_COUNT), replace=False)
    hsv_data = []

    for i in frame_indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ret, frame = cap.read()
        if ret:
            sel_x, sel_y, sel_w, sel_h = selection
            selected_area = frame[sel_y:sel_y + sel_h, sel_x:sel_x + sel_w]
            if selected_area.size == 0: continue
            hsv_area = cv2.cvtColor(selected_area, cv2.COLOR_BGR2HSV)
            hsv_data.extend(hsv_area.reshape(-1, 3))

    cap.release()
    os.remove(TEMP_VIDEO_FILE)  # Pulisci subito

    if not hsv_data:
        print("❌ Errore: Nessun pixel valido trovato.")
        return None

    hsv_data = np.array(hsv_data)
    mean = np.mean(hsv_data, axis=0)
    std = np.std(hsv_data, axis=0)

    # Calcolo statistico del range
    lower_bound = np.maximum(0, mean - std * 2).astype(int)
    upper_bound = np.minimum([179, 255, 255], mean + std * 2).astype(int)

    print(f"    -> Analisi completata. Range calcolato: {lower_bound.tolist()} -> {upper_bound.tolist()}")
    return {"lower": lower_bound.tolist(), "upper": upper_bound.tolist()}


def main():
    roi = load_roi()
    if not roi: return

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("❌ Errore: Impossibile accedere alla webcam.")
        return

    calibrated_data = {}
    print("--- Strumento di Calibrazione Interattiva ---")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Errore frame webcam. Riprovo...")
            time.sleep(0.5)
            continue

        # Disegna la ROI principale
        x, y, w, h = roi['x'], roi['y'], roi['w'], roi['h']
        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

        # Istruzioni
        draw_text_with_background(frame, "Premi 'r' (ROSSO), 'v' (VERDE), 's' (SPENTO) per registrare.", (10, 30))
        draw_text_with_background(frame, "Premi 'q' per SALVARE e USCIRE.", (10, 60))

        # Mostra stati già calibrati
        calibrated_text = "Stati Calibrati: " + ", ".join(calibrated_data.keys())
        draw_text_with_background(frame, calibrated_text, (10, frame.shape[0] - 20))

        cv2.imshow("Calibrazione", frame)
        key = cv2.waitKey(1) & 0xFF

        state_to_record = None
        if key == ord('r'):
            state_to_record = "ROSSO"
        elif key == ord('v'):
            state_to_record = "VERDE"
        elif key == ord('s'):
            state_to_record = "SPENTO"
        elif key == ord('q'):
            print("\nUscita dal programma di calibrazione.")
            break

        if state_to_record:
            print(f"\n[AVVIO CALIBRAZIONE PER: {state_to_record}]")
            # Rilascia la webcam principale per permettere alla funzione di usarla
            cap.release()
            cv2.destroyWindow("Calibrazione")

            color_range = record_and_analyze(state_to_record, roi)
            if color_range:
                calibrated_data[state_to_record] = color_range

            # Riconnetti alla webcam per il menu principale
            cap.open(CAMERA_INDEX)

    cap.release()
    cv2.destroyAllWindows()

    if len(calibrated_data) > 0:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(COLOR_CONFIG_FILE, 'w') as f:
            json.dump(calibrated_data, f, indent=4)
        print(f"\n🎉 Calibrazione salvata! {len(calibrated_data)} stati configurati in '{COLOR_CONFIG_FILE}'.")
    else:
        print("\nNessuno stato è stato calibrato. File di configurazione non salvato.")


if __name__ == "__main__":
    main()

