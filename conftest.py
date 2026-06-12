import sys
from pathlib import Path

# Put the project root on sys.path so tests in tests/ can `import server, agent`
# regardless of how pytest is invoked.
sys.path.insert(0, str(Path(__file__).parent))
