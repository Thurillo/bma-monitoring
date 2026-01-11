#File da portare su /home/pi

#!/bin/bash
python3 -m venv venv
source venv/bin/activate
cd bma-monitoring
python monitor_semaforo_TCS.py
