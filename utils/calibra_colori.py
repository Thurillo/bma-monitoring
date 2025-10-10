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

# --- CONFIGURAZIONE LAYOUT DASHBOARD ---
PANEL_WIDTH = 250
THUMBNAIL_HEIGHT = 100
COLOR_SWATCH_HEIGHT = 50
PADDING = 10

# Percorsi
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "..", "config")
COLOR_CONFIG_FILE = os.path.join(CONFIG_DIR, "color_ranges.json")
ROI_CONFIG_FILE = os.path.join(CONFIG_DIR, "roi_semaforo.json")


def load_roi():
    if not os.path.exists(ROI_CONFIG_FILE):
        print(f"❌ Errore: File ROI '{ROI_CONFIG_FILE}' non trovato. Esegui prima configura_zona.py.")
        return None
    with open(ROI_CONFIG_FILE, 'r') as f:
        return json.load(f)


def draw_text_with_background(frame, text, position, font_scale=0.6, color=(255, 255, 255), bg_color=(0, 0, 0)):
    (text_width, text_height), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)
    x, y = position
    cv2.rectangle(frame, (x, y - text_height - baseline), (x + text_width + PADDING, y + PADDING), bg_color, -1)
    cv2.putText(frame, text, (x + 5, y), cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, 1, cv2.LINE_AA)


def record_and_analyze(state_name):
    temp_video_file = os.path.join(SCRIPT_DIR, f"temp_{state_name}.avi")

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("❌ Errore webcam durante la registrazione.")
        return None, None

    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = int(cap.get(cv2.CAP_PROP_FPS)) or 20

    fourcc = cv2.VideoWriter_fourcc(*'XVID')
    out = cv2.VideoWriter(temp_video_file, fourcc, fps, (frame_width, frame_height))

    start_time = time.time()
    print(f"    -> Inizio registrazione per {state_name}...")
    while True:
        elapsed = time.time() - start_time
        if elapsed > RECORDING_SECONDS: break
        ret, frame = cap.read()
        if not ret: break
        countdown = RECORDING_SECONDS - int(elapsed)
        draw_text_with_background(frame, f"REGISTRAZIONE {state_name}: {countdown}s", (10, 30), color=(0, 0, 255))
        out.write(frame)
        cv2.imshow("Registrazione in corso...", frame)
        cv2.waitKey(1)

    print("    -> Registrazione completata.")
    cap.release()
    out.release()
    cv2.destroyWindow("Registrazione in corso...")

    cap = cv2.VideoCapture(temp_video_file)
    ret, first_frame = cap.read()
    if not ret:
        print("❌ Errore: impossibile leggere il video campione.")
        return None, None

    thumbnail = first_frame.copy()
    draw_text_with_background(thumbnail, "Disegna un rettangolo SULLA LUCE e premi INVIO", (10, 30),
                              color=(0, 255, 255))
    selection = cv2.selectROI("Calibrazione: Seleziona Campione Puro", thumbnail)
    cv2.destroyWindow("Calibrazione: Seleziona Campione Puro")

    if selection[2] == 0 or selection[3] == 0:
        print("    -> Selezione annullata.")
        os.remove(temp_video_file)
        return None, None

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
    os.remove(temp_video_file)

    if not hsv_data:
        print("❌ Errore: Nessun pixel valido trovato.")
        return None, None

    hsv_data = np.array(hsv_data)
    mean = np.mean(hsv_data, axis=0)
    std = np.std(hsv_data, axis=0)

    lower_bound = np.maximum(0, mean - std * 2).astype(int)
    upper_bound = np.minimum([179, 255, 255], mean + std * 2).astype(int)

    print(f"    -> Analisi completata. Range calcolato: {lower_bound.tolist()} -> {upper_bound.tolist()}")
    return {"lower": lower_bound.tolist(), "upper": upper_bound.tolist(),
            "mean_hsv": mean.astype(int).tolist()}, first_frame


def main():
    roi = load_roi()
    if not roi: return

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("❌ Errore: Impossibile accedere alla webcam.")
        return

    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    dashboard_width = frame_width + PANEL_WIDTH
    dashboard_height = frame_height

    calibrated_data = {}
    sample_thumbnails = {}

    print("--- Dashboard di Calibrazione Interattiva ---")

    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.5)
            continue

        # Crea la dashboard
        dashboard = np.zeros((dashboard_height, dashboard_width, 3), dtype=np.uint8)

        # 1. Inserisci il feed live
        dashboard[0:frame_height, 0:frame_width] = frame

        # 2. Disegna la ROI principale sul feed live
        x, y, w, h = roi['x'], roi['y'], roi['w'], roi['h']
        cv2.rectangle(dashboard, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(dashboard, "Campo Visivo", (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        # 3. Disegna le istruzioni
        draw_text_with_background(dashboard, "Premi 'r' (ROSSO), 'v' (VERDE), 's' (SPENTO)", (10, 30))
        draw_text_with_background(dashboard, "Premi 'q' per SALVARE e USCIRE", (10, 60))

        # 4. Costruisci il pannello di stato a destra
        panel_x_start = frame_width
        panel = dashboard[0:dashboard_height, panel_x_start:dashboard_width]
        panel.fill(40)  # Sfondo grigio scuro per il pannello

        section_height = dashboard_height // len(STATES_TO_CALIBRATE)

        for i, state_name in enumerate(STATES_TO_CALIBRATE):
            section_y_start = i * section_height

            # Titolo della sezione
            cv2.putText(panel, state_name, (PADDING, section_y_start + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (255, 255, 255), 2)

            thumb_y_start = section_y_start + 30
            thumb_placeholder = panel[thumb_y_start:thumb_y_start + THUMBNAIL_HEIGHT, PADDING:PANEL_WIDTH - PADDING]

            if state_name in sample_thumbnails:
                # Mostra l'anteprima del video
                thumb = cv2.resize(sample_thumbnails[state_name],
                                   (thumb_placeholder.shape[1], thumb_placeholder.shape[0]))
                thumb_placeholder[:, :] = thumb
            else:
                # Placeholder grigio
                thumb_placeholder[:, :] = (80, 80, 80)
                cv2.putText(thumb_placeholder, "N/A", (20, THUMBNAIL_HEIGHT // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                            (150, 150, 150), 2)

            color_y_start = thumb_y_start + THUMBNAIL_HEIGHT + PADDING
            color_placeholder = panel[color_y_start:color_y_start + COLOR_SWATCH_HEIGHT, PADDING:PANEL_WIDTH - PADDING]

            if state_name in calibrated_data:
                # Mostra il colore calibrato
                mean_hsv = np.uint8([[calibrated_data[state_name]['mean_hsv']]])
                mean_bgr = cv2.cvtColor(mean_hsv, cv2.COLOR_HSV2BGR)[0][0]
                color_placeholder[:, :] = (int(mean_bgr[0]), int(mean_bgr[1]), int(mean_bgr[2]))
            else:
                # Placeholder grigio
                color_placeholder[:, :] = (80, 80, 80)

        cv2.imshow("Dashboard di Calibrazione", dashboard)
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
            cap.release()
            cv2.destroyWindow("Dashboard di Calibrazione")

            color_range, thumbnail = record_and_analyze(state_to_record)
            if color_range and thumbnail is not None:
                calibrated_data[state_to_record] = color_range
                sample_thumbnails[state_to_record] = thumbnail

            cap.open(CAMERA_INDEX)

    cap.release()
    cv2.destroyAllWindows()

    if len(calibrated_data) > 0:
        # Pulisci i dati extra prima di salvare
        final_data_to_save = {k: {"lower": v["lower"], "upper": v["upper"]} for k, v in calibrated_data.items()}
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(COLOR_CONFIG_FILE, 'w') as f:
            json.dump(final_data_to_save, f, indent=4)
        print(f"\n🎉 Calibrazione salvata! {len(final_data_to_save)} stati configurati in '{COLOR_CONFIG_FILE}'.")
    else:
        print("\nNessuno stato è stato calibrato. File di configurazione non salvato.")


if __name__ == "__main__":
    main()

