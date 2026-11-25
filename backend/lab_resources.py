"""Measured, bounded reference work; never attempts host exhaustion."""
import hashlib
import time


def cpu_work(seconds=.15):
    if not 0 < seconds <= .25:
        raise ValueError('CPU sample duration must be within 250 milliseconds.')
    started, cpu = time.perf_counter(), time.thread_time()
    iterations, value = 0, b'controlled-workload'
    while time.perf_counter()-started < seconds:
        value = hashlib.sha256(value).digest()
        iterations += 1
    return {'operation_wall_ms': round((time.perf_counter()-started)*1000, 3),
            'operation_cpu_ms': round((time.thread_time()-cpu)*1000, 3), 'operation_iterations': iterations}


class AllocationBudget:
    def __init__(self):
        self.chunks = []
        self.limit = 8*1024*1024

    @property
    def retained(self):
        return sum(len(chunk) for chunk in self.chunks)

    def allocate(self):
        size = 2*1024*1024
        if self.retained+size > self.limit:
            raise MemoryError('Application allocation budget exceeded; no host OOM was induced.')
        self.chunks.append(bytearray(size))

    def reset(self):
        self.chunks.clear()
