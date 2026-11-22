import sys
from pathlib import Path

# Make the project root importable (services, db, app) when pytest runs from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
