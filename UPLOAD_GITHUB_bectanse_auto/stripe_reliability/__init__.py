"""Durable Stripe reconciliation; verified rights and existing post-payment workflows."""
import os


def enabled():
    return os.environ.get('STRIPE_RELIABILITY_ENABLED', 'false').lower() == 'true'
