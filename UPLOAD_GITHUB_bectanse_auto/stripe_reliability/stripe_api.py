"""Bounded HTTP retries, pagination and redacted errors; restricted keys only."""
import os
import time
import requests
from .environment import validate_key, validate_object


class StripeUnavailable(RuntimeError):
    def __init__(self, code, status=503):
        super().__init__(code)
        self.code, self.status = code, status


class StripeReader:
    def __init__(self, key=None, http=None, sleep=time.sleep):
        self.key = key or os.environ.get('STRIPE_RECONCILIATION_KEY') or os.environ.get('STRIPE_SECRET_KEY','')
        self.http, self.sleep = http or requests.Session(), sleep

    def request(self, method, path, params=None, *, idempotency=None, write_key=None):
        if not path.startswith('/') or '..' in path:
            raise ValueError('INVALID_STRIPE_PATH')
        key = write_key or self.key
        if not key:
            raise StripeUnavailable('STRIPE_KEY_MISSING')
        try:validate_key(key)
        except ValueError as error:raise StripeUnavailable(str(error)) from None
        headers = {'Stripe-Version': '2026-07-29.dahlia'}
        if idempotency:
            headers['Idempotency-Key'] = idempotency
        for attempt in range(3):
            try:
                response = self.http.request(method, 'https://api.stripe.com/v1'+path,
                    auth=(key,''), headers=headers, timeout=(4,15),
                    **({'params':params} if method=='GET' else {'data':params}))
            except requests.RequestException:
                if attempt==2:raise StripeUnavailable('STRIPE_NETWORK_ERROR') from None
                self.sleep(0.25 * 2**attempt);continue
            if response.status_code in (401,403):
                raise StripeUnavailable('STRIPE_READ_PERMISSION_REQUIRED' if method=='GET' else 'STRIPE_WRITE_PERMISSION_REQUIRED', response.status_code)
            if response.status_code==429 or response.status_code>=500:
                if attempt==2:raise StripeUnavailable('STRIPE_RATE_LIMIT' if response.status_code==429 else 'STRIPE_UNAVAILABLE')
                try:delay=float(response.headers.get('Retry-After','0.5'))
                except ValueError:delay=0.5
                self.sleep(min(2,max(0.25,delay)));continue
            if response.status_code==404:raise StripeUnavailable('STRIPE_OBJECT_MISSING',404)
            if not response.ok:raise StripeUnavailable('STRIPE_REQUEST_REJECTED',response.status_code)
            try:result=response.json()
            except ValueError:raise StripeUnavailable('STRIPE_INVALID_RESPONSE') from None
            try:validate_object(result)
            except ValueError as error:raise StripeUnavailable(str(error)) from None
            return result
        raise StripeUnavailable('STRIPE_UNAVAILABLE')

    def get(self, path, **params):
        return self.request('GET',path,params)

    def pages(self,path,**params):
        cursor=None
        seen=set()
        while True:
            page=self.get(path,limit=100,**params,**({'starting_after':cursor} if cursor else {}))
            rows=page.get('data',[])
            for row in rows:yield row
            if not page.get('has_more'):break
            if not rows or not rows[-1].get('id') or rows[-1]['id'] in seen:
                raise StripeUnavailable('STRIPE_PAGINATION_INVALID')
            cursor=rows[-1]['id'];seen.add(cursor)
