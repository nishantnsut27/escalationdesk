import unittest
from backend.lab_resources import AllocationBudget, cpu_work


class ResourceWorkloadTests(unittest.TestCase):
    def test_measured_cpu_is_bounded_and_performs_work(self):
        result = cpu_work(.005)
        self.assertGreater(result['operation_iterations'], 0)
        # Windows can report zero CPU time below its thread-counter resolution.
        # Completed iterations establish work without inventing a duration.
        self.assertGreaterEqual(result['operation_cpu_ms'], 0)
        self.assertGreaterEqual(result['operation_wall_ms'], 5)
        with self.assertRaises(ValueError):
            cpu_work(1)

    def test_allocation_guard_never_exceeds_eight_mib_and_resets(self):
        workload = AllocationBudget()
        for _ in range(4):
            workload.allocate()
        self.assertEqual(workload.retained, 8*1024*1024)
        with self.assertRaisesRegex(MemoryError, 'Application allocation budget'):
            workload.allocate()
        workload.reset()
        self.assertEqual(workload.retained, 0)
