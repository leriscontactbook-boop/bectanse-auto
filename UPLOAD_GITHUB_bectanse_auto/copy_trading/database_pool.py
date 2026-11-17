"""Exclusive reusable copy-control connections; no query or authorization cache."""
import os
import queue
import threading
import time


class Lease:
    def __init__(self,pool,connection):
        self.pool,self.connection=pool,connection
        self.broken=False
    def run(self,*args,**kwargs):
        if self.connection is None:raise RuntimeError('CONNECTION_LEASE_CLOSED')
        try:return self.connection.run(*args,**kwargs)
        except BaseException:
            self.broken=True
            raise  # Never replay a possibly committed command.
    def close(self):
        connection,self.connection=self.connection,None
        if connection is not None:self.pool.release(connection,self.broken)


class Pool:
    def __init__(self,factory,capacity=8,idle_seconds=60):
        self.factory,self.capacity,self.idle_seconds=factory,capacity,idle_seconds
        self.pid=os.getpid()
        self.idle=queue.LifoQueue(capacity)
        self.slots=threading.BoundedSemaphore(capacity)
    def acquire(self):
        if os.getpid()!=self.pid:raise RuntimeError('DATABASE_POOL_PROCESS_CHANGED')
        if not self.slots.acquire(timeout=5):raise RuntimeError('COPY_DATABASE_BUSY')
        connection=None
        try:
            while connection is None:
                try:created,candidate=self.idle.get_nowait()
                except queue.Empty:connection=self.factory();break
                if time.monotonic()-created>self.idle_seconds:
                    candidate.close()
                else:connection=candidate
            # DISCARD ALL on return removed all session settings and temporary
            # state. Each borrower receives the same bounded query timeouts.
            connection.run("SET statement_timeout='5000ms'")
            connection.run("SET lock_timeout='1000ms'")
            return Lease(self,connection)
        except BaseException:
            if connection is not None:
                try:connection.close()
                except Exception:pass
            self.slots.release()
            raise
    def release(self,connection,broken):
        try:
            if not broken:
                try:
                    connection.run('ROLLBACK')
                    connection.run('DISCARD ALL')
                except Exception:broken=True
            if broken:connection.close()
            else:self.idle.put_nowait((time.monotonic(),connection))
        finally:self.slots.release()


_pools={}
_lock=threading.Lock()

def connection(parameters):
    """Pool per process and exact database identity. Never log credentials."""
    import pg8000.native
    key=(os.getpid(),tuple(sorted(parameters.items())))
    with _lock:
        pool=_pools.get(key)
        if pool is None:
            pool=Pool(lambda:pg8000.native.Connection(**parameters,ssl_context=True,timeout=5))
            _pools[key]=pool
    return pool.acquire()
