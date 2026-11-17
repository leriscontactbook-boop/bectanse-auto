"""Explicit broker clock conversion. Never infer an offset from a quote."""
import math

def offset_seconds(value):
    if type(value) is not int or abs(value)>14*3600 or value%900:
        raise ValueError('INVALID_BROKER_TIME_OFFSET')
    return value

def quote_utc(time_msc, offset=0):
    offset=offset_seconds(offset)
    if type(time_msc) not in (int,float) or not math.isfinite(time_msc) or time_msc<=0:
        raise ValueError('INVALID_QUOTE_TIMESTAMP')
    return time_msc/1000-offset
