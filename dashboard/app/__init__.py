"""KI-Dashboard: holt morgens Daten aus san6 und Microsoft 365, lässt Claude sortieren, zeigt das Ergebnis."""
import os
import sys

# Das vorhandene Gateway (san6-Client) wiederverwenden
_GW = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "topm-gateway")
if _GW not in sys.path:
    sys.path.insert(0, _GW)
