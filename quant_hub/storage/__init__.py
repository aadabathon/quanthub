"""Local persistence for acquired market data (SQLite via stdlib sqlite3).

The storage layer knows about the data layer (canonical frames, date
ranges) and nothing else: no broker, no services, no GUI. See
``storage/bar_store.py`` for the schema and threading contract.
"""
