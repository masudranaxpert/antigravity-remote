#!/usr/bin/env python3
"""Antigravity Mobile Switcher - Entrypoint.

Starts the modular dashboard server with direct filesystem detection
and Secret Service credential switching.
"""
import sys
import os

# Ensure local package path resolution
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.server import run_server

if __name__ == "__main__":
    run_server()
