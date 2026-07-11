#!/bin/bash
cd "$(dirname "$0")"
exec ./.venv/bin/python3 probe_news_cache.py
