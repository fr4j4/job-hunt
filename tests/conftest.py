"""Config compartida de la suite (T-P2-4, T-P2-1)."""
import pathlib
import sys
import time

import pytest

# T-P2-4: raíz del repo relativa a este archivo (nada de rutas absolutas de otra
# máquina); ya la añade `python -m pytest` por cwd, esto es red de seguridad.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_sleep: no mockear time.sleep (tests que dependen de timing real entre hilos)",
    )


@pytest.fixture(autouse=True)
def _fast_sleep(request, monkeypatch):
    """T-P2-1: jobhunt.enrich y jobhunt.channel hacen `import time` a nivel de
    módulo/función, pero es el mismo objeto de sys.modules['time'] en todos
    lados → un solo patch del atributo .sleep alcanza a ambos. Los tests que
    simulan latencia real entre hilos se marcan @pytest.mark.real_sleep."""
    if request.node.get_closest_marker("real_sleep"):
        return
    monkeypatch.setattr(time, "sleep", lambda s: None)
