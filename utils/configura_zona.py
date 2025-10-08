import cv2
import json
import os

# --- COSTRUZIONE DINAMICA DEL PERCORSO ---
# Questo codice trova il percorso assoluto dello script attuale
# e poi naviga "indietro" per trovare la cartella radice del progetto.
# In questo modo, funzionerà sempre, indipendentemente da dove lo lanci.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..'))
CONFIG_DIR = os.path.join(PROJECT_ROOT, "bma-monitoring", "config")
CONFIG_FILE = os.path.join(CONFIG_DIR, "roi_semaforo.json")

CAMERA_INDEX = 0


def main():
    """
    Permette all'utente di selezionare una Region of Interest (ROI)
    dallo stream della webcam e salvarla in un file di configurazione JSON.
    """
    print("📸 Avvio configurazione ROI per il semaforo...")

    # Crea la directory di configurazione se non esiste
    # L'uso di exist_ok=True evita errori se la cartella esiste già
    print(f"⚙️  Salvataggio configurazione in: {CONFIG_DIR}")
    os.makedirs(CONFIG_DIR, exist_ok=True)

    # Inizializza la cattura video
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("❌ Errore: Impossibile accedere alla webcam.")
        return

    print("\nIstruzioni:")
    print("1. Verrà mostrata una finestra con il feed della webcam.")
    print("2. Clicca e trascina con il mouse per disegnare un rettangolo attorno al semaforo.")
    print("3. Una volta soddisfatto, PREMI INVIO o la BARRA SPAZIATRICE per confermare.")
    print("4. Per annullare e uscire, premi 'c'.")
    print("   (NON chiudere la finestra con la 'X' o la selezione non verrà salvata!)")

    # Cattura un singolo frame per la selezione
    ret, frame = cap.read()
    if not ret:
        print("❌ Errore: Impossibile catturare un frame dalla webcam.")
        cap.release()
        return

    # Permetti all'utente di selezionare la ROI
    roi = cv2.selectROI("Seleziona ROI", frame, fromCenter=False, showCrosshair=True)

    # Controlla se l'utente ha selezionato una ROI valida (larghezza e altezza > 0)
    if roi[2] > 0 and roi[3] > 0:
        config_data = {
            "x": int(roi[0]),
            "y": int(roi[1]),
            "w": int(roi[2]),
            "h": int(roi[3])
        }

        # Salva le coordinate in formato JSON
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config_data, f, indent=4)

            print(f"\n✅ ROI salvata con successo in '{CONFIG_FILE}'")
            print(f"   Coordinate: x={roi[0]}, y={roi[1]}, w={roi[2]}, h={roi[3]}")
        except Exception as e:
            print(f"\n❌ ERRORE CRITICO: Impossibile scrivere il file di configurazione.")
            print(f"   Dettagli: {e}")
            print(f"   Controlla i permessi della cartella: {CONFIG_DIR}")

    else:
        print("\n⚠️ Selezione annullata. Nessuna ROI salvata.")

    # Rilascia le risorse
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()