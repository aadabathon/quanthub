"""Online providers: public data fetched over the network (D43, W5).

Like ``importers/``, they turn what they read into ``data/`` types and
nothing more: no storage, no services, no GUI (``tests/test_layering.py``).
``services/`` caches and stores what they fetch. Each provider's terms are
checked before it ships (D34): the SEC's fair-access policy for
``sec_edgar``.
"""
