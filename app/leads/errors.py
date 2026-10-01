"""Errors of the leads package."""

from __future__ import annotations


class LeadDataError(ValueError):
    """The values given for a `BusinessLead` / `BusinessIdentity` (or a serialised lead) are invalid."""
