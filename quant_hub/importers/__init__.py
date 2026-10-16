"""File importers: data anyone has, in the formats brokers and vendors
export (D34).

They read files into plain frames and ``data/`` types, nothing more: no
storage, no services, no GUI (``tests/test_layering.py``).
``services/file_import.py`` validates and stores what they read, through
the same path as every other provider.
"""
