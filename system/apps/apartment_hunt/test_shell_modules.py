"""The app serves the two shell modules its page needs for the shared menu, and nothing else."""

from pathlib import Path

import pytest
from apartment_hunt.runner import app


def test_the_app_serves_only_the_shell_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    # The shell modules resolve from the repo root, where supervisord runs the app; this
    # suite runs from the app's own directory, so the test stands where the program does.
    monkeypatch.chdir(Path(__file__).resolve().parents[3])
    client = app.test_client()
    for basename in ("app_contract.js", "context_menu.js"):
        response = client.get(f"/_static/{basename}")
        assert response.status_code == 200
        assert response.mimetype == "text/javascript"
        assert response.data
    assert client.get("/_static/unknown.js").status_code == 404
