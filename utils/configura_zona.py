import cv2
import json
import os

# --- COSTANTI ---
CONFIG_DIR = "bma_monitoring/config"
CONFIG_FILE = os.path.join(CONFIG_DIR, "roi_semaforo.json")
CAMERA_INDEX = 0  # 0 per la prima webcam USB, cambia se necessario


def main():
    """
    Permette all'utente di selezionare una Region of Interest (ROI)
    dallo stream della webcam e salvarla in un file di configurazione JSON.
    """
    print("📸 Avvio configurazione ROI per il semaforo...")

    # Crea la directory di configurazione se non esiste
    os.makedirs(CONFIG_DIR, exist_ok=True)

    # Inizializza la cattura video
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("❌ Errore: Impossibile accedere alla webcam.")
        return

    print("\nIstruzioni:")
    print("1. Verrà mostrata una finestra con il feed della webcam.")
    print("2. Clicca e trascina con il mouse per disegnare un rettangolo attorno al semaforo.")
    print("3. Una volta soddisfatto, premi 'INVIO' o la 'BARRA SPAZIATRICE'.")
    print("4. Per annullare e uscire, premi 'c'.")

    # Cattura un singolo frame per la selezione
    ret, frame = cap.read()
    if not ret:
        print("❌ Errore: Impossibile catturare un frame dalla webcam.")
        cap.release()
        return

    # Permetti all'utente di selezionare la ROI
    # "Seleziona ROI" è il nome della finestra
    roi = cv2.selectROI("Seleziona ROI", frame, fromCenter=False, showCrosshair=True)

    # Controlla se l'utente ha selezionato una ROI valida
    if roi[2] > 0 and roi[3] > 0:
        config_data = {
            "x": roi[0],
            "y": roi[1],
            "w": roi[2],
            "h": roi[3]
        }

        # Salva le coordinate in formato JSON
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config_data, f, indent=4)

        print(f"\n✅ ROI salvata con successo in '{CONFIG_FILE}'")
        print(f"   Coordinate: x={roi[0]}, y={roi[1]}, w={roi[2]}, h={roi[3]}")
    else:
        print("\n⚠️ Selezione annullata. Nessuna ROI salvata.")

    # Rilascia le risorse
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()