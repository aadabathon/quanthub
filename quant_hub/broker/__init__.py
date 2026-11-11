"""Infrastructure boundary around brokerage connectivity.

This is the ONLY package allowed to import ``ibapi``. It exposes:

* ``base``          -- the ``Broker`` protocol, ``ConnectionState``, events.
* ``types``         -- contracts, bars, and the ``BrokerError`` hierarchy.
* ``ibkr``          -- the Interactive Brokers implementation.
* ``market_source`` -- adapter presenting a broker as a ``DataSource``.

v0.1 is strictly read-only: there is no order-placement code anywhere in this
package, by design.
"""
