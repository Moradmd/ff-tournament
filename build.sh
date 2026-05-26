#!/usr/bin/env bash
set -e
pip install -r requirements.txt
python -m py_compile app.py database.py config.py
python -c "from app import app; c=app.test_client(); assert c.get('/health').status_code==200"
echo "Build OK"
