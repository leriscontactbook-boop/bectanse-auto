"""Fail closed when a staged billing integration mixes test and live resources."""
import os
from . import enabled


def expected_mode():
    mode=os.environ.get('STRIPE_EXPECTED_MODE','').strip().lower()
    if mode not in {'','test','live'} or (enabled() and not mode):
        raise ValueError('STRIPE_EXPECTED_MODE_REQUIRED')
    return mode


def validate_key(key):
    mode=expected_mode()
    prefixes=('rk_'+mode+'_','sk_'+mode+'_')
    # Anonymous CLI sandboxes issue restricted rkcs_test keys. Never allow
    # this sandbox-only key class in a live environment.
    if mode=='test':prefixes+=('rkcs_test_',)
    if mode and not key.startswith(prefixes):
        raise ValueError('STRIPE_KEY_MODE_MISMATCH')


def validate_object(value):
    mode=expected_mode()
    if not mode or not isinstance(value,dict):return
    if 'livemode' in value and value['livemode'] is not (mode=='live'):
        raise ValueError('STRIPE_OBJECT_MODE_MISMATCH')
    for nested in value.values():
        if isinstance(nested,dict):validate_object(nested)
        elif isinstance(nested,list):
            for item in nested:validate_object(item)


def validate_event(event):
    mode=expected_mode()
    if mode and event.get('livemode') is not (mode=='live'):
        raise ValueError('STRIPE_EVENT_MODE_MISMATCH')
    validate_object(event)
