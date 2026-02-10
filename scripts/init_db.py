#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from db import init_db, DB_PATH

if __name__ == "__main__":
    init_db()
    print(f"Initialized DB at: {DB_PATH}")
