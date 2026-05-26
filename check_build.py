"""Run after build.ps1 — route smoke test."""
from app import app

client = app.test_client()
assert client.get("/health").status_code == 200
assert client.get("/").status_code == 200
print("routes OK")
