"""
api/services/twitter
---------------------
Twitter/X broadcasting module.

Consumes AlertDTO events from the EventDispatcher via the BroadcasterProtocol
interface, scores them, rate-limits, formats tweets, and posts via the
Twitter API v2 (or logs in dry-run mode).
"""
